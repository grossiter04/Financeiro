"""Fundamentos para a conta de Graham (LPA / VPA)."""

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


def _fetch_brapi(ticker: str, *, timeout: float) -> Fundamentos:
    url = f"https://brapi.dev/api/quote/{ticker}"
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        resp = client.get(url, params={"modules": "defaultKeyStatistics", "fundamental": "true"})
        resp.raise_for_status()
        payload = resp.json()

    results = payload.get("results") or []
    if not results:
        raise ValueError("brapi: sem resultado")

    item = results[0]
    stats = item.get("defaultKeyStatistics") or {}
    lpa = _num(stats.get("trailingEps")) or _num(item.get("earningsPerShare"))
    vpa = _num(stats.get("bookValue"))
    if lpa is None and vpa is None:
        raise ValueError("brapi: sem LPA/VPA")
    return Fundamentos(ticker=ticker, lpa=lpa, vpa=vpa, fonte="brapi.dev")


def _yahoo_crumb(client: httpx.Client) -> str:
    client.get("https://fc.yahoo.com")
    resp = client.get("https://query2.finance.yahoo.com/v1/test/getcrumb")
    resp.raise_for_status()
    crumb = resp.text.strip()
    if not crumb or "<" in crumb:
        raise ValueError("Yahoo: crumb inválido")
    return crumb


def _fetch_yahoo(ticker: str, *, timeout: float) -> Fundamentos:
    simbolo = _simbolo_yahoo(ticker)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        crumb = _yahoo_crumb(client)
        resp = client.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{simbolo}",
            params={"modules": "defaultKeyStatistics", "crumb": crumb},
        )
        resp.raise_for_status()
        payload = resp.json()

    results = (payload.get("quoteSummary") or {}).get("result") or []
    if not results:
        raise ValueError("Yahoo: sem resultado")
    stats = results[0].get("defaultKeyStatistics") or {}
    lpa = _num(stats.get("trailingEps"))
    vpa = _num(stats.get("bookValue"))
    if lpa is None and vpa is None:
        raise ValueError("Yahoo: sem LPA/VPA")
    return Fundamentos(ticker=ticker, lpa=lpa, vpa=vpa, fonte="Yahoo Finance")


def fetch_fundamentos(ticker: str, *, timeout: float = 15.0) -> Fundamentos:
    """Busca LPA (EPS) e VPA (book value) para a conta de Graham."""
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
