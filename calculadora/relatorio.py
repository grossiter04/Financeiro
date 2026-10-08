"""Relatório diário: notícias que afetam suas ações + números de cada uma."""

from __future__ import annotations

import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from calculadora.core import (
    BAZIN_DY,
    BaseMethod,
    YearCriterion,
    calculate,
    conta_bazin,
    conta_graham,
    resumo_datas_com,
)
from calculadora.fundamentos import fetch_fundamentos
from calculadora.mail import BRT, agora_brt, enviar_email, smtp_config_from_env
from calculadora.noticias import (
    Empresa,
    Noticia,
    _sem_acento,
    buscar_noticias,
    ligar_noticias,
    load_empresas,
    raiz_ticker,
)
from calculadora.preco import fetch_historico
from calculadora.proventos_fetch import fetch_proventos
from calculadora.setores import agrupar_por_setor, normalizar_setor, rotulo_setor
from calculadora.sinal import SinalOrientativo, calcular_sinal
from calculadora.storage import listar_acoes, obter_acao

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Ordem: modelos estáveis primeiro; 2.5-flash como rede de segurança se a família 3.x estiver em 503
GEMINI_MODELOS_PADRAO = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
MAX_NOTICIAS_IA = 40  # menos notícias → JSON da IA mais estável
_GEMINI_RETRIES_503 = 2
_GEMINI_ESPERA_503_S = 4.0

_POSITIVAS = [
    "alta", "sobe", "subiu", "dispara", "avanca", "lucro", "recorde", "supera",
    "eleva", "aprova", "dividendo", "proventos", "jcp", "recompra", "upgrade", "otimis",
]
_NEGATIVAS = [
    "queda", "cai ", "caiu", "despenca", "recua", "prejuizo", "rebaix", "multa",
    "investigacao", "greve", "corta", "downgrade", "pessimis", "risco", "perde", "tomba",
]


@dataclass
class ItemCarteira:
    ticker: str
    dy_desejado: float = 6.0
    n_anos: int = 5


@dataclass
class MetricaAcao:
    ticker: str
    nome: str
    setor: str
    preco: float | None = None
    var_dia: float | None = None
    var_mes: float | None = None
    teto_bazin: float | None = None
    pct_bazin: float | None = None
    teto_graham: float | None = None
    pct_graham: float | None = None
    teto_dy: float | None = None
    pct_dy: float | None = None
    dy_desejado: float = 6.0
    data_com_proxima: str = "—"
    data_com_ultima: str = "—"
    erros: list[str] = field(default_factory=list)


def _texto_proventos(ticker: str, *, timeout: float) -> tuple[str, str]:
    salva = obter_acao(ticker)
    if salva is not None and salva.proventos.strip():
        return salva.proventos, "banco local"
    got = fetch_proventos(ticker, timeout=timeout)
    return got.texto, got.fonte


@dataclass
class AnaliseNoticia:
    id: str
    impacto: str = "neutro"
    relevancia: int = 1
    acoes: list[str] = field(default_factory=list)
    explicacao: str = ""


@dataclass
class AnaliseAcao:
    ticker: str
    tendencia: str = "lateral"
    sentimento: str = "neutro"
    # True só com ≥1 notícia que cita a empresa e impacto positivo/negativo claro
    sentimento_confiante: bool = False
    expectativa: str = ""
    riscos: str = ""
    noticias: list[str] = field(default_factory=list)  # diretas + setor (contexto)
    noticias_diretas: list[str] = field(default_factory=list)  # só citação direta
    sinal: SinalOrientativo | None = None


@dataclass
class Relatorio:
    gerado_em: datetime
    metricas: list[MetricaAcao]
    noticias: list[Noticia]
    analise_noticias: dict[str, AnaliseNoticia]
    analise_acoes: dict[str, AnaliseAcao]
    resumo_mercado: str = ""
    destaques: list[str] = field(default_factory=list)
    motor: str = "regras"
    avisos: list[str] = field(default_factory=list)


def calcular_metricas(item: ItemCarteira, empresa: Empresa | None) -> MetricaAcao:
    m = MetricaAcao(
        ticker=item.ticker,
        nome=empresa.nome_principal if empresa else item.ticker,
        setor=empresa.setor if empresa else "outros",
        dy_desejado=item.dy_desejado,
    )
    try:
        hist = fetch_historico(item.ticker, periodo="1mo")
        fechamentos = [p.preco for p in hist.pontos if p.preco]
        if fechamentos:
            m.preco = fechamentos[-1]
            if len(fechamentos) >= 2 and fechamentos[-2]:
                m.var_dia = fechamentos[-1] / fechamentos[-2] - 1.0
            if fechamentos[0]:
                m.var_mes = fechamentos[-1] / fechamentos[0] - 1.0
    except Exception as exc:  # noqa: BLE001
        m.erros.append(f"preço: {exc}")

    try:
        texto, _ = _texto_proventos(item.ticker, timeout=20.0)
        datas = resumo_datas_com(texto)
        m.data_com_proxima = datas.fmt_proxima()
        m.data_com_ultima = datas.fmt_ultima()
        if m.preco is not None:
            res = calculate(
                texto,
                preco_atual=m.preco,
                dy_desejado=item.dy_desejado / 100.0,
                ticker=item.ticker,
                n_ultimos=item.n_anos,
                metodo=BaseMethod.MEDIA,
                criterio=YearCriterion.DATA_COM,
                incluir_ano_andamento=False,
            )
            m.teto_dy, m.pct_dy = res.preco_teto, res.diferenca
            if res.base and res.base > 0:
                baz = conta_bazin(res.base, m.preco, dy=BAZIN_DY)
                m.teto_bazin, m.pct_bazin = baz.preco_justo, baz.diferenca
    except Exception as exc:  # noqa: BLE001
        m.erros.append(f"proventos: {exc}")

    if m.preco is None:
        return m

    try:
        fund = fetch_fundamentos(item.ticker, timeout=15.0)
        if fund.lpa and fund.vpa and fund.lpa > 0 and fund.vpa > 0:
            gra = conta_graham(fund.lpa, fund.vpa, m.preco)
            m.teto_graham, m.pct_graham = gra.preco_justo, gra.diferenca
    except Exception as exc:  # noqa: BLE001
        m.erros.append(f"Graham: {exc}")
    return m


# ---------- análise sem IA ----------


def _impacto_por_palavras(n: Noticia) -> str:
    texto = _sem_acento(f"{n.titulo} {n.resumo}") + " "
    pos = sum(texto.count(p) for p in _POSITIVAS)
    neg = sum(texto.count(p) for p in _NEGATIVAS)
    if pos and neg:
        return "misto"
    if pos:
        return "positivo"
    if neg:
        return "negativo"
    return "neutro"


def _tendencia(m: MetricaAcao) -> str:
    ref = m.var_mes if m.var_mes is not None else m.var_dia
    if ref is None:
        return "sem dados"
    if ref > 0.02:
        return "alta"
    if ref < -0.02:
        return "queda"
    return "lateral"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.1f}%"


def sentimento_de_diretas(
    ticker: str,
    noticias: list[Noticia],
    an_not: dict[str, AnaliseNoticia],
) -> tuple[str, bool, list[str]]:
    """
    Sentimento só com notícias que citam a empresa (acoes_diretas).

    Confiança exige pelo menos uma notícia direta com impacto positivo ou negativo
    (neutro/misto sozinho não puxa o sinal).
    """
    t = ticker.upper()
    diretas = [n for n in noticias if t in n.acoes_diretas]
    ids = [n.id for n in diretas]
    if not diretas:
        return "neutro", False, []

    pos = sum(1 for n in diretas if an_not.get(n.id, AnaliseNoticia(n.id)).impacto == "positivo")
    neg = sum(1 for n in diretas if an_not.get(n.id, AnaliseNoticia(n.id)).impacto == "negativo")
    if pos > neg:
        return "positivo", True, ids
    if neg > pos:
        return "negativo", True, ids
    return "neutro", False, ids


def analise_por_regras(
    metricas: list[MetricaAcao], noticias: list[Noticia]
) -> tuple[dict[str, AnaliseNoticia], dict[str, AnaliseAcao], str]:
    an_not: dict[str, AnaliseNoticia] = {}
    for n in noticias:
        # No modo rígido, "acoes" da análise = só citação direta (não setor)
        an_not[n.id] = AnaliseNoticia(
            id=n.id,
            impacto=_impacto_por_palavras(n),
            relevancia=3 if n.acoes_diretas else 1,
            acoes=list(n.acoes_diretas),
            explicacao=(
                f"Cita diretamente: {', '.join(n.acoes_diretas)}."
                if n.acoes_diretas
                else f"Tema: {', '.join(n.temas)} (contexto de setor; não mexe no sentimento)."
            ),
        )

    an_acoes: dict[str, AnaliseAcao] = {}
    for m in metricas:
        ligadas = [n for n in noticias if m.ticker in n.acoes]
        sentimento, confiante, ids_diretas = sentimento_de_diretas(m.ticker, noticias, an_not)
        pos = sum(
            1
            for nid in ids_diretas
            if an_not.get(nid, AnaliseNoticia(nid)).impacto == "positivo"
        )
        neg = sum(
            1
            for nid in ids_diretas
            if an_not.get(nid, AnaliseNoticia(nid)).impacto == "negativo"
        )

        partes = [f"Preço {_pct(m.var_mes)} no mês e {_pct(m.var_dia)} no último pregão."]
        if m.pct_bazin is not None:
            partes.append(
                f"Está {abs(m.pct_bazin) * 100:.1f}% {'abaixo' if m.pct_bazin < 0 else 'acima'} do teto Bazin."
            )
        if m.pct_graham is not None:
            partes.append(
                f"Está {abs(m.pct_graham) * 100:.1f}% {'abaixo' if m.pct_graham < 0 else 'acima'} do preço de Graham."
            )
        if ids_diretas:
            partes.append(
                f"{len(ids_diretas)} notícia(s) citando a empresa ({pos} positiva(s), {neg} negativa(s))."
            )
            if not confiante:
                partes.append("Sem consenso claro de impacto — sentimento não altera o sinal.")
        else:
            setor = [n for n in ligadas if m.ticker not in n.acoes_diretas]
            if setor:
                partes.append(
                    f"Sem citação direta; {len(setor)} notícia(s) de setor só como contexto "
                    "(não entram no sentimento nem no sinal)."
                )
            else:
                partes.append("Sem notícias ligadas a esta ação.")

        an_acoes[m.ticker] = AnaliseAcao(
            ticker=m.ticker,
            tendencia=_tendencia(m),
            sentimento=sentimento,
            sentimento_confiante=confiante,
            expectativa=" ".join(partes),
            noticias=[n.id for n in ligadas],
            noticias_diretas=ids_diretas,
            sinal=calcular_sinal(
                pct_bazin=m.pct_bazin,
                pct_graham=m.pct_graham,
                sentimento=sentimento,
                usar_sentimento=confiante,
            ),
        )

    resumo = (
        "Análise automática rígida (sem IA): sentimento e sinal só usam notícias que "
        "citam a empresa. Configure GEMINI_API_KEY para resumo do mercado e textos "
        "mais elaborados (o vínculo notícia↔ação continua filtrado)."
    )
    return an_not, an_acoes, resumo


def _anexar_sinais(
    metricas: list[MetricaAcao], an_acoes: dict[str, AnaliseAcao]
) -> None:
    """Recalcula o sinal após a IA (ou regras) atualizar o sentimento."""
    por_ticker = {m.ticker: m for m in metricas}
    for ticker, an in an_acoes.items():
        m = por_ticker.get(ticker)
        an.sinal = calcular_sinal(
            pct_bazin=m.pct_bazin if m else None,
            pct_graham=m.pct_graham if m else None,
            sentimento=an.sentimento,
            usar_sentimento=an.sentimento_confiante,
        )


# ---------- análise com Gemini ----------


def _prompt(metricas: list[MetricaAcao], noticias: list[Noticia], data_txt: str) -> str:
    acoes = [
        {
            "ticker": m.ticker,
            "empresa": m.nome,
            "setor": m.setor,
            "preco": m.preco,
            "var_ultimo_pregao_pct": None if m.var_dia is None else round(m.var_dia * 100, 2),
            "var_mes_pct": None if m.var_mes is None else round(m.var_mes * 100, 2),
            "margem_bazin_pct": None if m.pct_bazin is None else round(m.pct_bazin * 100, 1),
            "margem_graham_pct": None if m.pct_graham is None else round(m.pct_graham * 100, 1),
        }
        for m in metricas
    ]
    lista_noticias = [
        {
            "id": n.id,
            "fonte": n.fonte,
            "titulo": n.titulo,
            "resumo": n.resumo[:400],
            "citadas": n.acoes_diretas,
            "temas": n.temas,
        }
        for n in noticias
    ]
    return f"""Você é um analista de ações brasileiras. Hoje é {data_txt}.
O investidor acompanha as ações abaixo (foco em dividendos; margem negativa = preço abaixo do teto, ou seja, barata).

AÇÕES:
{json.dumps(acoes, ensure_ascii=False)}

NOTÍCIAS DAS ÚLTIMAS HORAS (sites brasileiros de investimento):
{json.dumps(lista_noticias, ensure_ascii=False)}

Tarefa, em português do Brasil:
1. "resumo_mercado": 4 a 7 frases sobre o cenário do dia (juros, dólar, Ibovespa, política, exterior) e o que isso significa para a carteira.
2. "destaques": 3 a 6 frases curtas com o que o investidor mais precisa saber hoje.
3. "noticias": só notícias que CITAM diretamente alguma ação (campo "citadas" não vazio). Ignore manchetes só de tema/setor sem citar a empresa. Para cada uma: id, impacto ("positivo", "negativo", "neutro" ou "misto"), relevancia (1 a 5), acoes (SOMENTE tickers que aparecem em "citadas" daquela notícia — nunca invente ticker) e explicacao (1 a 3 frases).
4. "acoes": TODAS as ações da lista. Para cada uma: ticker, tendencia ("alta", "queda" ou "lateral", só com base na variação de preço), sentimento ("positivo", "negativo", "neutro" ou "misto" — só com notícias em que o ticker está em "citadas"; se não houver citação direta, sentimento deve ser "neutro"), expectativa (2 a 4 frases), riscos (1 a 2 frases) e noticias (ids só de notícias que citam essa ação).

Regras rígidas:
- Use apenas as notícias fornecidas; não invente fatos, números nem ligações empresa↔manchete.
- Notícia de Selic/setor NÃO conta como sentimento da ação, a menos que "citadas" inclua o ticker.
- Se não houver notícia direta sobre uma ação, diga isso e baseie-se no preço/valuation.
- Não dê ordem de compra ou venda; descreva o cenário.

Responda SOMENTE com JSON no formato:
{{"resumo_mercado": "", "destaques": [""], "noticias": [{{"id": "", "impacto": "", "relevancia": 1, "acoes": [""], "explicacao": ""}}], "acoes": [{{"ticker": "", "tendencia": "", "sentimento": "", "expectativa": "", "riscos": "", "noticias": [""]}}]}}"""


def _reparar_json_texto(texto: str) -> str:
    """Corrige falhas comuns da IA (vírgula sobrando, cercas, comentários)."""
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)
    # Remove vírgulas finais antes de } ou ]
    texto = re.sub(r",(\s*[}\]])", r"\1", texto)
    # Remove comentários de linha estilo //
    texto = re.sub(r"(?m)^\s*//.*?$", "", texto)
    return texto.strip()


def _extrair_json(texto: str) -> dict:
    bruto = _reparar_json_texto(texto)
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    if inicio < 0 or fim < 0 or fim <= inicio:
        raise ValueError("resposta da IA sem JSON")
    pedaco = bruto[inicio : fim + 1]
    try:
        return json.loads(pedaco)
    except json.JSONDecodeError:
        # Segunda passagem: às vezes sobra vírgula após reparo parcial
        pedaco2 = re.sub(r",(\s*[}\]])", r"\1", pedaco)
        return json.loads(pedaco2)


def chamar_gemini(prompt: str, *, api_key: str, modelos: list[str], timeout: float = 240.0) -> tuple[dict, str]:
    erros: list[str] = []
    with httpx.Client(timeout=timeout) as client:
        for modelo in modelos:
            # Modelos 2.x às vezes rejeitam thinkingConfig; tenta com e sem
            variantes = [True, False] if "gemini-3" in modelo else [False, True]
            ok_http = False
            for com_thinking in variantes:
                corpo = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "maxOutputTokens": 8192,
                        **(
                            {"thinkingConfig": {"thinkingLevel": "low"}}
                            if com_thinking
                            else {}
                        ),
                    },
                }
                resp = None
                for tentativa in range(_GEMINI_RETRIES_503 + 1):
                    try:
                        resp = client.post(
                            GEMINI_URL.format(model=modelo),
                            headers={
                                "x-goog-api-key": api_key,
                                "Content-Type": "application/json",
                            },
                            json=corpo,
                        )
                    except Exception as exc:  # noqa: BLE001
                        erros.append(f"{modelo}: {exc}")
                        resp = None
                        break
                    if resp.status_code == 503 and tentativa < _GEMINI_RETRIES_503:
                        time.sleep(_GEMINI_ESPERA_503_S * (tentativa + 1))
                        continue
                    break
                if resp is None:
                    break
                if resp.status_code == 400 and com_thinking:
                    # thinkingConfig rejeitado → tenta sem
                    continue
                if resp.status_code != 200:
                    erros.append(
                        f"{modelo}: HTTP {resp.status_code} {resp.text[:220]}"
                    )
                    break
                ok_http = True
                try:
                    payload = resp.json()
                    partes = payload["candidates"][0]["content"]["parts"]
                    texto = "".join(p.get("text", "") for p in partes if "text" in p)
                    if not texto.strip():
                        raise ValueError("resposta vazia (só thinking?)")
                    return _extrair_json(texto), modelo
                except Exception as exc:  # noqa: BLE001
                    erros.append(f"{modelo}: {exc}")
                    break
            if not ok_http:
                continue
    raise RuntimeError("; ".join(erros) or "nenhum modelo disponível")


_IMPACTOS_OK = frozenset({"positivo", "negativo", "neutro", "misto"})


def _aplicar_ia(
    dados: dict,
    metricas: list[MetricaAcao],
    noticias: list[Noticia],
    an_not: dict[str, AnaliseNoticia],
    an_acoes: dict[str, AnaliseAcao],
) -> tuple[str, list[str]]:
    """
    Aplica texto da IA, mas o vínculo notícia↔ação e o sentimento são
    recalculados de forma rígida (só citação direta).
    """
    ids = {n.id for n in noticias}
    tickers = {m.ticker for m in metricas}
    por_id = {n.id: n for n in noticias}

    # Mantém impactos por palavra das regras quando a IA não classificar bem.
    # Importante: mesclar na análise existente — nunca apagar notícias que a IA omitiu
    # (senão a seção "Notícias que podem mexer…" some).
    impacto_regras = {nid: a.impacto for nid, a in an_not.items()}

    for raw in dados.get("noticias") or []:
        nid = str(raw.get("id", ""))
        n = por_id.get(nid)
        if n is None:
            continue
        try:
            relev = int(raw.get("relevancia", 1))
        except (TypeError, ValueError):
            relev = 1
        relev = max(1, min(5, relev))
        impacto = str(raw.get("impacto") or "").strip().lower()
        if impacto not in _IMPACTOS_OK:
            impacto = impacto_regras.get(nid, _impacto_por_palavras(n))
        explicacao = str(raw.get("explicacao") or "").strip()
        prev = an_not.get(nid)

        if n.acoes_diretas:
            # Só tickers realmente citados na manchete (rígido para o sentimento)
            acoes_ok = [t for t in n.acoes_diretas if t in tickers]
            if not acoes_ok:
                continue
            an_not[nid] = AnaliseNoticia(
                id=nid,
                impacto=impacto,
                relevancia=relev,
                acoes=acoes_ok,
                explicacao=explicacao or (prev.explicacao if prev else ""),
            )
        else:
            # Setor/tema: enriquece texto, mas acoes=[] (não mexe no sentimento)
            an_not[nid] = AnaliseNoticia(
                id=nid,
                impacto=impacto,
                relevancia=relev,
                acoes=[],
                explicacao=explicacao
                or (prev.explicacao if prev else f"Tema: {', '.join(n.temas)} (contexto)."),
            )

    # Textos da IA (expectativa/riscos); sentimento sempre recalculado
    textos_ia: dict[str, tuple[str, str, str]] = {}
    for raw in dados.get("acoes") or []:
        t = str(raw.get("ticker", "")).upper()
        if t not in tickers:
            continue
        textos_ia[t] = (
            str(raw.get("tendencia") or "").lower(),
            str(raw.get("expectativa") or ""),
            str(raw.get("riscos") or ""),
        )

    for m in metricas:
        prev = an_acoes.get(m.ticker) or AnaliseAcao(m.ticker)
        sentimento, confiante, ids_diretas = sentimento_de_diretas(
            m.ticker, noticias, an_not
        )
        tend_ia, exp_ia, riscos_ia = textos_ia.get(m.ticker, ("", "", ""))
        tendencia = tend_ia if tend_ia in ("alta", "queda", "lateral") else prev.tendencia
        an_acoes[m.ticker] = AnaliseAcao(
            ticker=m.ticker,
            tendencia=tendencia,
            sentimento=sentimento,
            sentimento_confiante=confiante,
            expectativa=exp_ia or prev.expectativa,
            riscos=riscos_ia or prev.riscos,
            noticias=prev.noticias or ids_diretas,
            noticias_diretas=ids_diretas,
        )

    destaques = [str(d) for d in (dados.get("destaques") or []) if str(d).strip()]
    return str(dados.get("resumo_mercado") or ""), destaques


# ---------- montagem ----------


def _itens_carteira() -> list[ItemCarteira]:
    return [
        ItemCarteira(ticker=a.ticker, dy_desejado=float(a.dy or 6.0))
        for a in listar_acoes()
    ]


def gerar_relatorio(
    *,
    empresas_path: Path | None = None,
    usar_ia: bool = True,
    horas: int = 36,
) -> Relatorio:
    agora = agora_brt()
    itens = _itens_carteira()
    empresas = load_empresas(empresas_path)
    tickers = [i.ticker for i in itens]
    if not tickers:
        return Relatorio(
            gerado_em=agora,
            metricas=[],
            noticias=[],
            analise_noticias={},
            analise_acoes={},
            resumo_mercado="Nenhuma ação salva no banco. Cadastre tickers na calculadora antes de gerar o relatório.",
            motor="regras",
            avisos=["Carteira vazia."],
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futuro_noticias = pool.submit(buscar_noticias, horas=horas)
        metricas = list(pool.map(lambda i: calcular_metricas(i, empresas.get(raiz_ticker(i.ticker))), itens))
        todas, avisos = futuro_noticias.result()

    ligar_noticias(todas, tickers, empresas)
    relevantes = [n for n in todas if n.relevante]
    relevantes.sort(key=lambda n: (len(n.acoes_diretas) == 0, -len(n.acoes)))
    relevantes = relevantes[:MAX_NOTICIAS_IA]

    an_not, an_acoes, resumo = analise_por_regras(metricas, relevantes)
    destaques: list[str] = []
    motor = "regras"

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if usar_ia and api_key:
        env_modelo = os.environ.get("GEMINI_MODEL", "").strip()
        modelos = [env_modelo] + [m for m in GEMINI_MODELOS_PADRAO if m != env_modelo] if env_modelo else GEMINI_MODELOS_PADRAO
        try:
            dados, modelo = chamar_gemini(_prompt(metricas, relevantes, agora.strftime("%d/%m/%Y")), api_key=api_key, modelos=modelos)
            resumo_ia, destaques = _aplicar_ia(dados, metricas, relevantes, an_not, an_acoes)
            resumo = resumo_ia or resumo
            motor = modelo
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"IA indisponível, usei análise simples: {exc}")
            resumo = (
                "A IA do Google não respondeu desta vez (detalhes em \"Avisos técnicos\", no fim). "
                "Abaixo, a análise automática simples."
            )
    elif usar_ia:
        avisos.append("GEMINI_API_KEY não configurada: análise simples, sem IA.")

    for m in metricas:
        for e in m.erros:
            avisos.append(f"{m.ticker}: {e}")

    _anexar_sinais(metricas, an_acoes)

    return Relatorio(
        gerado_em=agora,
        metricas=metricas,
        noticias=relevantes,
        analise_noticias=an_not,
        analise_acoes=an_acoes,
        resumo_mercado=resumo,
        destaques=destaques,
        motor=motor,
        avisos=avisos,
    )


# ---------- renderização ----------

_COR = {
    "positivo": "#3f6f5a", "alta": "#3f6f5a",
    "negativo": "#8a5a5a", "queda": "#8a5a5a",
    "misto": "#7a6f5d", "neutro": "#64748b", "lateral": "#64748b", "sem dados": "#94a3b8",
    "compra": "#3f6f5a", "aguardar": "#7a6f5d", "cautela": "#8a5a5a", "sem_dados": "#6b7280",
    "atrativa": "#3f6f5a", "neutra": "#7a6f5d", "esticada": "#8a5a5a",
}
_SETA = {"alta": "▲", "queda": "▼", "lateral": "▶", "sem dados": "·"}


def _e(texto: object) -> str:
    return html.escape(str(texto or ""))


def _badge(rotulo: str) -> str:
    cor = _COR.get(rotulo, "#475569")
    return (
        f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;'
        f'background:{cor};color:#fff;font-size:12px">{_e(rotulo)}</span>'
    )


def _badge_sinal(sinal: SinalOrientativo) -> str:
    return (
        f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;'
        f'background:{sinal.cor};color:#fff;font-size:12px" title="{_e(sinal.resumo)}">'
        f"{_e(sinal.rotulo)}</span>"
    )


def _cor_pct(v: float | None, *, barato_negativo: bool = False) -> str:
    if v is None:
        return "#94a3b8"
    bom = v < 0 if barato_negativo else v > 0
    return "#15803d" if bom else "#b91c1c" if v != 0 else "#475569"


def _td_pct(v: float | None, *, barato_negativo: bool = False) -> str:
    return f'<td style="padding:6px;text-align:right;color:{_cor_pct(v, barato_negativo=barato_negativo)}">{_pct(v)}</td>'


def _chave_acao(rel: Relatorio, m: MetricaAcao) -> tuple:
    an = rel.analise_acoes.get(m.ticker)
    diretas = sum(1 for n in rel.noticias if m.ticker in n.acoes_diretas)
    return (-diretas, -(len(an.noticias) if an else 0), m.ticker)


def _ordem_acoes(rel: Relatorio) -> list[MetricaAcao]:
    """Ordem plana (compat); preferir `_grupos_por_setor` na renderização."""
    return sorted(rel.metricas, key=lambda m: _chave_acao(rel, m))


def _grupos_por_setor(rel: Relatorio) -> list[tuple[str, list[MetricaAcao]]]:
    return agrupar_por_setor(
        rel.metricas,
        setor_de=lambda m: normalizar_setor(m.setor),
        chave_item=lambda m: _chave_acao(rel, m),
    )


def _cabecalho_tabela_acoes() -> str:
    return (
        '<table style="border-collapse:collapse;width:100%;font-size:13px">'
        '<tr style="background:#f1f5f9;text-align:left">'
        '<th style="padding:6px">Ação</th><th style="padding:6px;text-align:right">Preço</th>'
        '<th style="padding:6px">Próx. com</th><th style="padding:6px">Últ. com</th>'
        '<th style="padding:6px;text-align:right">Dia</th><th style="padding:6px;text-align:right">Mês</th>'
        '<th style="padding:6px;text-align:right">vs Bazin</th><th style="padding:6px;text-align:right">vs Graham</th>'
        '<th style="padding:6px">Tendência</th><th style="padding:6px">Sentimento</th>'
        '<th style="padding:6px">Sinal</th><th style="padding:6px">Notícias</th></tr>'
    )


def _linha_tabela_acao(rel: Relatorio, m: MetricaAcao) -> str:
    an = rel.analise_acoes.get(m.ticker) or AnaliseAcao(m.ticker)
    preco = "—" if m.preco is None else f"R$ {m.preco:.2f}"
    tend = an.tendencia
    sinal = an.sinal or calcular_sinal(
        pct_bazin=m.pct_bazin,
        pct_graham=m.pct_graham,
        sentimento=an.sentimento,
        usar_sentimento=an.sentimento_confiante,
    )
    sent_label = an.sentimento if an.sentimento_confiante else f"{an.sentimento}*"
    n_dir = len(an.noticias_diretas)
    return (
        '<tr style="border-bottom:1px solid #e2e8f0">'
        f'<td style="padding:6px"><b>{_e(m.ticker)}</b><br><span style="color:#64748b">{_e(m.nome)}</span></td>'
        f'<td style="padding:6px;text-align:right">{preco}</td>'
        f'<td style="padding:6px;white-space:nowrap">{_e(m.data_com_proxima)}</td>'
        f'<td style="padding:6px;white-space:nowrap">{_e(m.data_com_ultima)}</td>'
        + _td_pct(m.var_dia)
        + _td_pct(m.var_mes)
        + _td_pct(m.pct_bazin, barato_negativo=True)
        + _td_pct(m.pct_graham, barato_negativo=True)
        + f'<td style="padding:6px;color:{_COR.get(tend, "#475569")}">{_SETA.get(tend, "")} {_e(tend)}</td>'
        f'<td style="padding:6px">{_badge(sent_label)}</td>'
        f'<td style="padding:6px">{_badge_sinal(sinal)}</td>'
        f'<td style="padding:6px;text-align:center">{n_dir}</td></tr>'
    )


def _noticias_ordenadas(rel: Relatorio) -> list[Noticia]:
    def chave(n: Noticia):
        an = rel.analise_noticias.get(n.id)
        return (-(an.relevancia if an else 0), n.acoes_diretas == [])

    return sorted([n for n in rel.noticias if n.id in rel.analise_noticias], key=chave)


def renderizar_html(rel: Relatorio) -> str:
    data_txt = rel.gerado_em.strftime("%d/%m/%Y %H:%M")
    por_id = {n.id: n for n in rel.noticias}
    h: list[str] = [
        '<div style="font-family:Segoe UI,Arial,sans-serif;max-width:900px;color:#0f172a">',
        f'<h2 style="margin-bottom:4px">Relatório das suas ações — {data_txt}</h2>',
        f'<div style="color:#64748b;font-size:13px">{len(rel.metricas)} ações · '
        f'{len(rel.noticias)} notícias relevantes · análise: {_e(rel.motor)}</div>',
        '<h3>Cenário do dia</h3>',
        f'<p style="line-height:1.5">{_e(rel.resumo_mercado)}</p>',
    ]
    if rel.destaques:
        h.append("<ul>" + "".join(f"<li>{_e(d)}</li>" for d in rel.destaques) + "</ul>")

    h.append("<h3>Suas ações por setor</h3>")
    for setor, grupo in _grupos_por_setor(rel):
        h.append(
            f'<h4 style="margin:18px 0 8px;color:#0f172a;border-bottom:2px solid #cbd5e1;'
            f'padding-bottom:4px">{_e(rotulo_setor(setor))} '
            f'<span style="color:#64748b;font-weight:500;font-size:13px">({len(grupo)})</span></h4>'
        )
        h.append(_cabecalho_tabela_acoes())
        for m in grupo:
            h.append(_linha_tabela_acao(rel, m))
        h.append("</table>")
    h.append(
        '<p style="color:#64748b;font-size:12px">vs Bazin / vs Graham: negativo (verde) = '
        "preço abaixo do teto. Sentimento com * = sem citação direta clara "
        "(não altera o sinal). Coluna Notícias = só citações diretas. "
        "Sinal = indicativo, não é ordem de compra/venda.</p>"
    )

    h.append("<h3>O que esperar de cada ação</h3>")
    for setor, grupo in _grupos_por_setor(rel):
        h.append(
            f'<h4 style="margin:16px 0 6px;color:#334155">{_e(rotulo_setor(setor))}</h4>'
        )
        for m in grupo:
            an = rel.analise_acoes.get(m.ticker)
            if an is None:
                continue
            sinal = an.sinal or calcular_sinal(
                pct_bazin=m.pct_bazin,
                pct_graham=m.pct_graham,
                sentimento=an.sentimento,
                usar_sentimento=an.sentimento_confiante,
            )
            sent_badge = an.sentimento if an.sentimento_confiante else f"{an.sentimento}*"
            h.append(
                '<div style="border-left:4px solid '
                f'{_COR.get(an.sentimento, "#475569")};padding:6px 12px;margin:10px 0">'
                f"<b>{_e(m.ticker)}</b> — {_e(m.nome)} &nbsp;{_badge(an.tendencia)} "
                f"{_badge(sent_badge)} {_badge_sinal(sinal)}"
                f'<p style="margin:6px 0;font-size:13px;color:#475569">{_e(sinal.resumo)}</p>'
                f'<p style="margin:6px 0">{_e(an.expectativa)}</p>'
            )
            if an.riscos:
                h.append(
                    f'<p style="margin:6px 0;color:#b45309"><b>Riscos:</b> {_e(an.riscos)}</p>'
                )
            ids_links = an.noticias_diretas or an.noticias
            links = [por_id[i] for i in ids_links if i in por_id][:5]
            if links:
                rotulo = "Notícias diretas" if an.noticias_diretas else "Notícias (contexto)"
                h.append(
                    f'<div style="font-size:12px;color:#64748b;margin-top:4px">{rotulo}:</div>'
                    '<ul style="margin:4px 0;font-size:13px">'
                    + "".join(
                        f'<li><a href="{_e(n.link)}">{_e(n.titulo)}</a> '
                        f"<i>({_e(n.fonte)})</i></li>"
                        for n in links
                    )
                    + "</ul>"
                )
            h.append("</div>")

    h.append("<h3>Notícias que podem mexer com a carteira</h3>")
    noticias_bloco = _noticias_ordenadas(rel)
    if not noticias_bloco:
        h.append(
            '<p style="color:#64748b;font-size:13px">Nenhuma notícia relevante nas últimas horas '
            "para as ações salvas.</p>"
        )
    for n in noticias_bloco:
        an = rel.analise_noticias[n.id]
        hora = n.publicado.astimezone(BRT).strftime("%d/%m %H:%M") if n.publicado else ""
        tipo = "citação direta" if n.acoes_diretas else "contexto de setor"
        afeta = ", ".join(an.acoes) if an.acoes else (
            ", ".join(n.acoes_tema) + " (setor)" if n.acoes_tema else "—"
        )
        h.append(
            '<div style="padding:8px 0;border-bottom:1px solid #e2e8f0">'
            f'<a href="{_e(n.link)}" style="font-weight:600;color:#1d4ed8">{_e(n.titulo)}</a><br>'
            f'<span style="color:#64748b;font-size:12px">{_e(n.fonte)} · {hora} · '
            f"relevância {an.relevancia}/5 · {_e(tipo)}</span> "
            f"{_badge(an.impacto)}"
            f'<div style="font-size:13px;margin-top:4px"><b>Afeta:</b> {_e(afeta)}</div>'
            f'<div style="font-size:13px">{_e(an.explicacao)}</div></div>'
        )

    if rel.avisos:
        h.append(
            '<details style="margin-top:16px;color:#64748b;font-size:12px"><summary>Avisos técnicos</summary><ul>'
            + "".join(f"<li>{_e(a)}</li>" for a in rel.avisos[:40])
            + "</ul></details>"
        )
    h.append(
        '<p style="color:#94a3b8;font-size:11px;margin-top:16px">Relatório automático a partir de notícias públicas. '
        "Não é recomendação de investimento.</p></div>"
    )
    return "\n".join(h)


def renderizar_texto(rel: Relatorio) -> str:
    linhas = [f"Relatório das suas ações — {rel.gerado_em.strftime('%d/%m/%Y %H:%M')}", ""]
    linhas += ["CENÁRIO DO DIA", rel.resumo_mercado, ""]
    for d in rel.destaques:
        linhas.append(f"- {d}")
    linhas += ["", "SUAS AÇÕES POR SETOR"]
    for setor, grupo in _grupos_por_setor(rel):
        linhas.append(f"\n## {rotulo_setor(setor)} ({len(grupo)})")
        for m in grupo:
            an = rel.analise_acoes.get(m.ticker) or AnaliseAcao(m.ticker)
            preco = "—" if m.preco is None else f"R$ {m.preco:.2f}"
            sinal = an.sinal or calcular_sinal(
                pct_bazin=m.pct_bazin,
                pct_graham=m.pct_graham,
                sentimento=an.sentimento,
                usar_sentimento=an.sentimento_confiante,
            )
            sent = an.sentimento if an.sentimento_confiante else f"{an.sentimento}*"
            linhas.append(
                f"{m.ticker} {preco} | próx. com {m.data_com_proxima} | "
                f"últ. com {m.data_com_ultima} | dia {_pct(m.var_dia)} | "
                f"mês {_pct(m.var_mes)} | Bazin {_pct(m.pct_bazin)} | "
                f"Graham {_pct(m.pct_graham)} | {an.tendencia} / {sent} | {sinal.rotulo}"
            )
            if an.expectativa:
                linhas.append(f"  {an.expectativa}")
            if an.riscos:
                linhas.append(f"  Riscos: {an.riscos}")
    linhas += ["", "NOTÍCIAS"]
    for n in _noticias_ordenadas(rel):
        an = rel.analise_noticias[n.id]
        linhas.append(f"[{an.impacto}] {n.titulo} ({n.fonte})")
        linhas.append(f"  Afeta: {', '.join(an.acoes) or '—'} — {an.explicacao}")
        linhas.append(f"  {n.link}")
    linhas += ["", "Não é recomendação de investimento."]
    return "\n".join(linhas)


def enviar_relatorio(rel: Relatorio) -> None:
    smtp = smtp_config_from_env()
    if not smtp:
        raise ValueError("Configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD e ALERT_EMAIL_TO.")
    enviar_email(
        renderizar_texto(rel),
        host=str(smtp["host"]),
        port=int(smtp["port"]),
        user=str(smtp["user"]),
        password=str(smtp["password"]),
        para=str(smtp["para"]),
        de=str(smtp["de"]),
        assunto=f"Relatório do dia {rel.gerado_em.strftime('%d/%m')}: notícias das suas ações",
        html=renderizar_html(rel),
    )
