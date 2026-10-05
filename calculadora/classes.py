"""Descoberta e comparação de classes da mesma empresa (ON/PN/Unit)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from calculadora.core import (
    BAZIN_DY,
    BaseMethod,
    YearCriterion,
    calculate,
    conta_bazin,
    conta_graham,
)
from calculadora.fundamentos import fetch_fundamentos
from calculadora.preco import _simbolo_yahoo, fetch_preco
from calculadora.proventos_fetch import fetch_proventos

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Sufixos comuns na B3 para classes da mesma empresa
_SUFIXOS = ("3", "4", "5", "6", "7", "8", "11")

_TIPO = {
    "3": "ON",
    "4": "PN",
    "5": "PNA",
    "6": "PNB",
    "7": "PNC",
    "8": "PND",
    "11": "Unit",
}


@dataclass
class ClasseComparativo:
    ticker: str
    tipo: str
    preco: float
    base: float | None
    dy_atual: float | None
    teto_usuario: float | None
    pct_usuario: float | None
    teto_bazin: float | None
    pct_bazin: float | None
    veredito_bazin: str
    teto_graham: float | None
    pct_graham: float | None
    veredito_graham: str
    avisos: list[str]


@dataclass
class ComponenteUnit:
    quantidade: int
    tipo: str
    ticker: str


@dataclass
class AnalisePrecoUnit:
    unit_ticker: str
    preco_unit: float
    componentes: list[tuple[str, int, float, float]]  # ticker, qtd, preço, subtotal
    soma_componentes: float
    diff_reais: float
    pct_diff: float
    veredito: str


def raiz_ticker(ticker: str) -> str:
    """ITUB4 → ITUB; TAEE11 → TAEE."""
    t = ticker.strip().upper().removesuffix(".SA")
    return re.sub(r"\d+$", "", t)


def sufixo_ticker(ticker: str) -> str:
    t = ticker.strip().upper().removesuffix(".SA")
    m = re.search(r"(\d+)$", t)
    return m.group(1) if m else ""


def tipo_classe(ticker: str) -> str:
    suf = sufixo_ticker(ticker)
    return _TIPO.get(suf, suf or "?")


def candidatos_classes(ticker: str) -> list[str]:
    raiz = raiz_ticker(ticker)
    if len(raiz) < 3:
        t = ticker.strip().upper().removesuffix(".SA")
        return [t] if t else []
    return [f"{raiz}{s}" for s in _SUFIXOS]


def _ticker_cotado(ticker: str, *, timeout: float = 10.0) -> float | None:
    """Devolve preço se o ticker existir no Yahoo; senão None."""
    simbolo = _simbolo_yahoo(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
            resp = client.get(url, params={"interval": "1d", "range": "5d"})
            if resp.status_code != 200:
                return None
            results = (resp.json().get("chart") or {}).get("result") or []
            if not results:
                return None
            preco = (results[0].get("meta") or {}).get("regularMarketPrice")
            return float(preco) if preco is not None else None
    except Exception:  # noqa: BLE001
        return None


def fetch_composicao_unit(ticker: str, *, timeout: float = 20.0) -> list[ComponenteUnit] | None:
    """
    Composição da unit (ex.: SANB11 = 1 ON + 1 PN) via Status Invest.
    Só se aplica a tickers terminados em 11.
    """
    t = ticker.strip().upper().removesuffix(".SA")
    if sufixo_ticker(t) != "11":
        return None
    slug = t.lower()
    url = f"https://statusinvest.com.br/acoes/{slug}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception:  # noqa: BLE001
        return None

    idx = html.find("UNIT</strong>")
    if idx < 0:
        return None
    chunk = html[idx : idx + 2500]
    m = re.search(r'<div class="unit[^"]*"[^>]*>(.*?)</div>\s*</div>', chunk, re.S | re.I)
    if not m:
        return None

    componentes: list[ComponenteUnit] = []
    for qty, tipo, tick in re.findall(
        r"(\d+)\s+(ON|PN|PNA|PNB|PND|PNC).*?\(([A-Z0-9]+)\)",
        m.group(1),
        re.I,
    ):
        componentes.append(
            ComponenteUnit(quantidade=int(qty), tipo=tipo.upper(), ticker=tick.upper())
        )
    return componentes or None


def analisar_preco_unit(
    linhas: list[ClasseComparativo],
    *,
    timeout: float = 20.0,
) -> AnalisePrecoUnit | None:
    """
    Compara preço da unit com a soma dos componentes cotados separadamente.
    pct_diff negativo = unit mais barata que montar ON/PN avulsos.
    """
    units = [c for c in linhas if sufixo_ticker(c.ticker) == "11"]
    if not units:
        return None

    unit = units[0]
    composicao = fetch_composicao_unit(unit.ticker, timeout=timeout)
    if not composicao:
        return None

    precos = {c.ticker: c.preco for c in linhas}
    detalhes: list[tuple[str, int, float, float]] = []
    soma = 0.0
    for comp in composicao:
        preco = precos.get(comp.ticker)
        if preco is None:
            preco = _ticker_cotado(comp.ticker, timeout=min(timeout, 12.0))
        if preco is None or preco <= 0:
            return None
        subtotal = comp.quantidade * preco
        soma += subtotal
        detalhes.append((comp.ticker, comp.quantidade, preco, subtotal))

    if soma <= 0:
        return None

    diff = unit.preco - soma
    pct = unit.preco / soma - 1.0
    if pct < -0.001:
        ver = (
            f"Unit mais barata: {abs(pct) * 100:.2f}% "
            f"(economia de R$ {abs(diff):.2f} por unit)"
        )
    elif pct > 0.001:
        ver = (
            f"Montar separado mais barato: {pct * 100:.2f}% "
            f"(R$ {abs(diff):.2f} a menos que a unit)"
        )
    else:
        ver = "Preços equivalentes (unit ≈ soma dos componentes)"

    return AnalisePrecoUnit(
        unit_ticker=unit.ticker,
        preco_unit=unit.preco,
        componentes=detalhes,
        soma_componentes=soma,
        diff_reais=diff,
        pct_diff=pct,
        veredito=ver,
    )


def melhor_por_margem(
    linhas: list[ClasseComparativo],
    attr: str,
) -> ClasseComparativo | None:
    """Menor pct = mais barato (margem negativa é melhor)."""
    candidatas = [c for c in linhas if getattr(c, attr, None) is not None]
    if not candidatas:
        return None
    return min(candidatas, key=lambda c: getattr(c, attr))


def listar_classes_existentes(ticker: str, *, timeout: float = 10.0) -> list[tuple[str, float]]:
    """
    Lista (ticker, preço) das classes irmãs que existem na B3.
    Inclui o próprio ticker se cotado.
    """
    achados: list[tuple[str, float]] = []
    for cand in candidatos_classes(ticker):
        preco = _ticker_cotado(cand, timeout=timeout)
        if preco is not None and preco > 0:
            achados.append((cand, preco))
    return achados


def comparar_classes(
    ticker: str,
    *,
    dy_desejado: float,
    n_ultimos: int | None = 5,
    anos_especificos: list[int] | None = None,
    metodo: BaseMethod = BaseMethod.MEDIA,
    criterio: YearCriterion = YearCriterion.DATA_COM,
    incluir_ano_andamento: bool = False,
    proventos_conhecidos: dict[str, str] | None = None,
    timeout: float = 25.0,
) -> list[ClasseComparativo]:
    """
    Compara ON/PN/Unit da mesma empresa por DY e margem Bazin.
    proventos_conhecidos: mapa ticker→texto já carregado (ex.: aba atual).
    """
    conhecidos = {k.upper(): v for k, v in (proventos_conhecidos or {}).items()}
    existentes = listar_classes_existentes(ticker, timeout=min(timeout, 12.0))
    if len(existentes) < 2:
        return []

    linhas: list[ClasseComparativo] = []
    for tick, preco in existentes:
        avisos: list[str] = []
        texto = conhecidos.get(tick, "")
        if not texto.strip():
            try:
                got = fetch_proventos(tick, timeout=timeout)
                texto = got.texto
                if got.avisos:
                    avisos.extend(got.avisos[:3])
            except Exception as exc:  # noqa: BLE001
                avisos.append(f"Proventos: {exc}")
                linhas.append(
                    ClasseComparativo(
                        ticker=tick,
                        tipo=tipo_classe(tick),
                        preco=preco,
                        base=None,
                        dy_atual=None,
                        teto_usuario=None,
                        pct_usuario=None,
                        teto_bazin=None,
                        pct_bazin=None,
                        veredito_bazin="Sem proventos",
                        teto_graham=None,
                        pct_graham=None,
                        veredito_graham="—",
                        avisos=avisos,
                    )
                )
                continue

        # Confirma preço fresco se possível
        try:
            preco = float(fetch_preco(tick, timeout=min(timeout, 12.0)).preco)
        except Exception:  # noqa: BLE001
            pass

        result = calculate(
            texto,
            preco_atual=preco,
            dy_desejado=dy_desejado,
            ticker=tick,
            n_ultimos=n_ultimos,
            anos_especificos=anos_especificos,
            metodo=metodo,
            criterio=criterio,
            incluir_ano_andamento=incluir_ano_andamento,
        )

        teto_b = pct_b = None
        ver_b = "—"
        if result.base is not None and result.base > 0 and preco > 0:
            baz = conta_bazin(result.base, preco, dy=BAZIN_DY)
            teto_b = baz.preco_justo
            pct_b = baz.diferenca
            ver_b = baz.veredito

        teto_g = pct_g = None
        ver_g = "—"
        try:
            fund = fetch_fundamentos(tick, timeout=min(timeout, 12.0))
            if (
                fund.lpa is not None
                and fund.vpa is not None
                and fund.lpa > 0
                and fund.vpa > 0
            ):
                gra = conta_graham(fund.lpa, fund.vpa, preco)
                teto_g = gra.preco_justo
                pct_g = gra.diferenca
                ver_g = gra.veredito
        except Exception:  # noqa: BLE001
            pass

        linhas.append(
            ClasseComparativo(
                ticker=tick,
                tipo=tipo_classe(tick),
                preco=preco,
                base=result.base,
                dy_atual=result.dy_atual,
                teto_usuario=result.preco_teto,
                pct_usuario=result.diferenca,
                teto_bazin=teto_b,
                pct_bazin=pct_b,
                veredito_bazin=ver_b,
                teto_graham=teto_g,
                pct_graham=pct_g,
                veredito_graham=ver_g,
                avisos=avisos + (result.erros[:2] if result.erros else []),
            )
        )

    # Mais negativa = mais barata no Bazin
    linhas.sort(
        key=lambda c: (
            c.pct_bazin is None,
            c.pct_bazin if c.pct_bazin is not None else 0.0,
        )
    )
    return linhas
