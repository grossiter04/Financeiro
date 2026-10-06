"""Notícias de investimento via RSS e ligação notícia → ações acompanhadas."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EMPRESAS = _ROOT / "alertas" / "empresas.toml"

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
}

FEEDS: dict[str, str] = {
    "InfoMoney": "https://www.infomoney.com.br/feed/",
    "InvestNews": "https://investnews.com.br/feed/",
    "Money Times": "https://www.moneytimes.com.br/feed/",
    "Seu Dinheiro": "https://www.seudinheiro.com/feed/",
    "Investing.com": "https://br.investing.com/rss/news_25.rss",
    "Valor Investe": "https://valorinveste.globo.com/rss/valorinveste/",
    "Suno": "https://www.suno.com.br/noticias/feed/",
    "E-Investidor": "https://einvestidor.estadao.com.br/feed/",
}

# tema → (palavras-chave, setores afetados). "estatal" é tratado à parte.
TEMAS: dict[str, tuple[list[str], list[str]]] = {
    "juros e inflação": (
        ["selic", "copom", "juros", "di futuro", "banco central", "inflacao", "ipca", "tesouro direto"],
        ["bancos", "seguros", "energia", "bolsa", "varejo", "construcao", "saneamento"],
    ),
    "dólar e câmbio": (
        ["dolar", "cambio"],
        ["papel", "mineracao", "petroleo", "industria", "agro"],
    ),
    "petróleo": (
        ["petroleo", "brent", "opep", "combustive", "gasolina", "diesel", "pre-sal"],
        ["petroleo"],
    ),
    "minério e China": (
        ["minerio", "siderurg", "economia chinesa", "pib da china", "estimulos da china", "demanda chinesa"],
        ["mineracao"],
    ),
    "eleição e governo": (
        [
            "segundo turno", "primeiro turno", "1º turno", "2º turno", "eleicao presidencial",
            "pesquisa eleitoral", "pesquisas eleitorais", "rali eleitoral", "flavio bolsonaro",
            "lula", "estatais", "privatiza", "fiscal", "arcabouco",
        ],
        ["estatal", "bolsa"],
    ),
    "setor elétrico": (
        ["aneel", "tarifa de energia", "transmissao de energia", "leilao de energia", "bandeira tarifaria", "hidrolog"],
        ["energia"],
    ),
    "crédito e bancos": (
        ["inadimplencia", "credito bancario", "spread bancario", "febraban"],
        ["bancos"],
    ),
    "seguros": (["susep", "seguradora"], ["seguros"]),
    "celulose": (["celulose", "papelao"], ["papel"]),
    "Ibovespa": (["ibovespa"], ["bolsa"]),
}


@dataclass
class Empresa:
    raiz: str
    nomes: list[str] = field(default_factory=list)
    nomes_exatos: list[str] = field(default_factory=list)
    setor: str = "outros"
    estatal: bool = False
    ignorar: list[str] = field(default_factory=list)

    @property
    def nome_principal(self) -> str:
        return (self.nomes or self.nomes_exatos or [self.raiz])[0]


@dataclass
class Noticia:
    id: str
    titulo: str
    resumo: str
    link: str
    fonte: str
    publicado: datetime | None
    acoes_diretas: list[str] = field(default_factory=list)
    temas: list[str] = field(default_factory=list)
    acoes_tema: list[str] = field(default_factory=list)

    @property
    def acoes(self) -> list[str]:
        vistos: list[str] = []
        for t in self.acoes_diretas + self.acoes_tema:
            if t not in vistos:
                vistos.append(t)
        return vistos

    @property
    def relevante(self) -> bool:
        return bool(self.acoes_diretas or self.acoes_tema)


def _sem_acento(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def _limpar_html(texto: str) -> str:
    sem_tags = re.sub(r"<[^>]+>", " ", texto or "")
    return re.sub(r"\s+", " ", html.unescape(sem_tags)).strip()


def raiz_ticker(ticker: str) -> str:
    return re.sub(r"\d+$", "", ticker.strip().upper().removesuffix(".SA"))


def load_empresas(path: Path | None = None) -> dict[str, Empresa]:
    path = path or DEFAULT_EMPRESAS
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        cfg = tomllib.load(f) or {}
    out: dict[str, Empresa] = {}
    for raiz, raw in cfg.items():
        if not isinstance(raw, dict):
            continue
        r = raiz.strip().upper()
        out[r] = Empresa(
            raiz=r,
            nomes=[str(n) for n in raw.get("nomes") or []],
            nomes_exatos=[str(n) for n in raw.get("nomes_exatos") or []],
            setor=str(raw.get("setor") or "outros"),
            estatal=bool(raw.get("estatal", False)),
            ignorar=[str(n) for n in raw.get("ignorar") or []],
        )
    return out


def _parse_data(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_rss(xml_text: str, fonte: str) -> list[Noticia]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    noticias: list[Noticia] = []
    for item in root.iter("item"):
        titulo = _limpar_html(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        if not titulo or not link:
            continue
        resumo = _limpar_html(item.findtext("description") or "")
        noticias.append(
            Noticia(
                id=hashlib.sha1(link.encode("utf-8")).hexdigest()[:8],
                titulo=titulo,
                resumo=resumo[:600],
                link=link,
                fonte=fonte,
                publicado=_parse_data(item.findtext("pubDate")),
            )
        )
    return noticias


def _baixar_feed(fonte: str, url: str, timeout: float) -> tuple[list[Noticia], str | None]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_UA) as client:
            resp = client.get(url)
            resp.raise_for_status()
        itens = parse_rss(resp.text, fonte)
        if not itens:
            return [], f"{fonte}: feed sem itens legíveis"
        return itens, None
    except Exception as exc:  # noqa: BLE001
        return [], f"{fonte}: {exc}"


def buscar_noticias(
    *,
    horas: int = 36,
    feeds: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> tuple[list[Noticia], list[str]]:
    """Baixa todos os feeds em paralelo; devolve notícias recentes sem duplicatas."""
    feeds = feeds or FEEDS
    todas: list[Noticia] = []
    avisos: list[str] = []
    with ThreadPoolExecutor(max_workers=len(feeds)) as pool:
        futuros = [pool.submit(_baixar_feed, f, u, timeout) for f, u in feeds.items()]
        for fut in futuros:
            itens, aviso = fut.result()
            todas.extend(itens)
            if aviso:
                avisos.append(aviso)

    limite = datetime.now(timezone.utc) - timedelta(hours=horas)
    vistos: set[str] = set()
    recentes: list[Noticia] = []
    for n in todas:
        if n.publicado is not None and n.publicado < limite:
            continue
        chave = _sem_acento(n.titulo)
        if chave in vistos:
            continue
        vistos.add(chave)
        recentes.append(n)
    recentes.sort(
        key=lambda n: n.publicado or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return recentes, avisos


def _tem_palavra(texto_norm: str, palavra: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(_sem_acento(palavra))}", texto_norm) is not None


def _cita_empresa(texto: str, emp: Empresa) -> bool:
    for frase in emp.ignorar:
        texto = texto.replace(frase, " ")
    texto_norm = _sem_acento(texto)
    for nome in emp.nomes:
        alvo = _sem_acento(nome)
        if re.search(rf"(?<![\w]){re.escape(alvo)}(?![\w])", texto_norm):
            return True
    for nome in emp.nomes_exatos:
        if re.search(rf"(?<![\w]){re.escape(nome)}(?![\w])", texto):
            return True
    return False


def ligar_noticias(
    noticias: list[Noticia],
    tickers: list[str],
    empresas: dict[str, Empresa],
) -> None:
    """Preenche acoes_diretas / temas / acoes_tema de cada notícia (in-place)."""
    por_raiz: dict[str, list[str]] = {}
    for t in tickers:
        por_raiz.setdefault(raiz_ticker(t), []).append(t.upper())

    for n in noticias:
        texto = f"{n.titulo} {n.resumo}"
        texto_norm = _sem_acento(texto)
        texto_upper = texto.upper()

        diretas: list[str] = []
        for raiz, lista in por_raiz.items():
            emp = empresas.get(raiz)
            cita_ticker = re.search(rf"\b{re.escape(raiz)}\d{{1,2}}\b", texto_upper)
            if cita_ticker or (emp and _cita_empresa(texto, emp)):
                diretas.extend(lista)

        temas: list[str] = []
        setores: set[str] = set()
        for tema, (palavras, afetados) in TEMAS.items():
            if any(_tem_palavra(texto_norm, p) for p in palavras):
                temas.append(tema)
                setores.update(afetados)

        por_tema: list[str] = []
        if setores:
            for raiz, lista in por_raiz.items():
                emp = empresas.get(raiz)
                if emp is None:
                    continue
                if emp.setor in setores or ("estatal" in setores and emp.estatal):
                    por_tema.extend(t for t in lista if t not in diretas)

        n.acoes_diretas = diretas
        n.temas = temas
        n.acoes_tema = por_tema
