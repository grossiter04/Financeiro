"""Fundamentos (LPA/VPA/P/L + EV/FCF/ROE) para valuation e tese."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from calculadora.preco import _simbolo_yahoo

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class Fundamentos:
    ticker: str
    lpa: float | None  # lucro por ação (trailing)
    vpa: float | None  # valor patrimonial por ação
    fonte: str
    pl: float | None = None  # P/L trailing
    payout: float | None = None  # fração 0–1+
    ev: float | None = None  # enterprise value
    ebitda: float | None = None
    ebit: float | None = None
    fcf: float | None = None  # free cash flow TTM
    roe: float | None = None  # return on equity (fração)
    divida: float | None = None  # total debt
    caixa: float | None = None
    beta: float | None = None
    acoes: float | None = None  # shares outstanding


def _num(valor) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, dict):
        valor = valor.get("raw", valor.get("fmt"))
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _yahoo_crumb(client: httpx.Client) -> str:
    client.get("https://fc.yahoo.com")
    resp = client.get("https://query2.finance.yahoo.com/v1/test/getcrumb")
    resp.raise_for_status()
    crumb = resp.text.strip()
    if not crumb or "<" in crumb:
        raise ValueError("Yahoo: crumb inválido")
    return crumb


def _ev_ebit(ev: float | None, ebit: float | None, ebitda: float | None) -> float | None:
    """EV/EBIT preferencial; fallback EV/EBITDA."""
    if ev is None or ev <= 0:
        return None
    if ebit is not None and ebit > 0:
        return ev / ebit
    if ebitda is not None and ebitda > 0:
        return ev / ebitda
    return None


def _fcf_yield(fcf: float | None, ev: float | None) -> float | None:
    if fcf is None or ev is None or ev <= 0:
        return None
    return fcf / ev


def _earnings_yield(pl: float | None) -> float | None:
    if pl is None or pl <= 0:
        return None
    return 1.0 / pl


def _p_vp(preco: float | None, vpa: float | None) -> float | None:
    if preco is None or preco <= 0 or vpa is None or vpa <= 0:
        return None
    return preco / vpa


def _fetch_brapi(ticker: str, *, timeout: float) -> Fundamentos:
    url = f"https://brapi.dev/api/quote/{ticker}"
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        resp = client.get(
            url,
            params={"modules": "defaultKeyStatistics,financialData", "fundamental": "true"},
        )
        resp.raise_for_status()
        payload = resp.json()

    results = payload.get("results") or []
    if not results:
        raise ValueError("brapi: sem resultado")

    item = results[0]
    stats = item.get("defaultKeyStatistics") or {}
    fin = item.get("financialData") or {}
    lpa = _num(stats.get("trailingEps")) or _num(item.get("earningsPerShare"))
    vpa = _num(stats.get("bookValue"))
    pl = _num(item.get("priceEarnings")) or _num(stats.get("trailingPE"))
    payout = _num(stats.get("payoutRatio"))
    ev = _num(stats.get("enterpriseValue"))
    ebitda = _num(fin.get("ebitda"))
    ebit = _num(fin.get("ebit"))
    fcf = _num(fin.get("freeCashflow"))
    roe = _num(fin.get("returnOnEquity"))
    divida = _num(fin.get("totalDebt"))
    caixa = _num(fin.get("totalCash"))
    beta = _num(stats.get("beta"))
    acoes = _num(stats.get("sharesOutstanding"))
    if lpa is None and vpa is None and pl is None and fcf is None and ev is None:
        raise ValueError("brapi: sem fundamentos")
    return Fundamentos(
        ticker=ticker,
        lpa=lpa,
        vpa=vpa,
        fonte="brapi.dev",
        pl=pl,
        payout=payout,
        ev=ev,
        ebitda=ebitda,
        ebit=ebit,
        fcf=fcf,
        roe=roe,
        divida=divida,
        caixa=caixa,
        beta=beta,
        acoes=acoes,
    )


def _fetch_yahoo(ticker: str, *, timeout: float) -> Fundamentos:
    simbolo = _simbolo_yahoo(ticker)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        crumb = _yahoo_crumb(client)
        resp = client.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{simbolo}",
            params={
                "modules": "defaultKeyStatistics,summaryDetail,financialData",
                "crumb": crumb,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

    results = (payload.get("quoteSummary") or {}).get("result") or []
    if not results:
        raise ValueError("Yahoo: sem resultado")
    r0 = results[0]
    stats = r0.get("defaultKeyStatistics") or {}
    summary = r0.get("summaryDetail") or {}
    fin = r0.get("financialData") or {}
    lpa = _num(stats.get("trailingEps"))
    vpa = _num(stats.get("bookValue"))
    pl = _num(summary.get("trailingPE")) or _num(stats.get("trailingPE"))
    payout = _num(stats.get("payoutRatio"))
    ev = _num(stats.get("enterpriseValue"))
    ebitda = _num(fin.get("ebitda"))
    ebit = _num(fin.get("ebit"))
    fcf = _num(fin.get("freeCashflow"))
    roe = _num(fin.get("returnOnEquity"))
    divida = _num(fin.get("totalDebt"))
    caixa = _num(fin.get("totalCash"))
    beta = _num(stats.get("beta"))
    acoes = _num(stats.get("sharesOutstanding"))
    if lpa is None and vpa is None and pl is None and fcf is None and ev is None:
        raise ValueError("Yahoo: sem fundamentos")
    return Fundamentos(
        ticker=ticker,
        lpa=lpa,
        vpa=vpa,
        fonte="Yahoo Finance",
        pl=pl,
        payout=payout,
        ev=ev,
        ebitda=ebitda,
        ebit=ebit,
        fcf=fcf,
        roe=roe,
        divida=divida,
        caixa=caixa,
        beta=beta,
        acoes=acoes,
    )


def fetch_fundamentos(ticker: str, *, timeout: float = 15.0) -> Fundamentos:
    """Busca LPA/VPA/P/L e, se possível, EV/FCF/ROE/dívida."""
    t = ticker.strip().upper().removesuffix(".SA")
    if not t:
        raise ValueError("ticker vazio")

    erros: list[str] = []
    try:
        return _fetch_brapi(t, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        erros.append(f"brapi: {exc}")
    try:
        return _fetch_yahoo(t, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        erros.append(f"yahoo: {exc}")
    raise ValueError(" | ".join(erros))
