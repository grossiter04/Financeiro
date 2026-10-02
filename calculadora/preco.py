"""Cotação quase em tempo real via Yahoo Finance (B3: TICKER.SA)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

_B3_TZ = ZoneInfo("America/Sao_Paulo")


@dataclass
class Cotacao:
    ticker: str
    preco: float
    horario: datetime | None
    moeda: str
    fonte: str


def _simbolo_yahoo(ticker: str) -> str:
    t = ticker.strip().upper()
    if not t:
        raise ValueError("ticker vazio")
    if t.endswith(".SA"):
        return t
    return f"{t}.SA"


def fetch_preco(ticker: str, *, timeout: float = 15.0) -> Cotacao:
    """
    Busca o preço mais recente no Yahoo Finance.
    Em pregão costuma atualizar com atraso baixo (segundos a poucos minutos).
    Fora do pregão, devolve o último negócio do dia.
    """
    simbolo = _simbolo_yahoo(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}"
    params = {"interval": "1m", "range": "1d"}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        payload = resp.json()

    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"Yahoo Finance: {chart['error']}")

    results = chart.get("result") or []
    if not results:
        raise ValueError(f"Sem cotação para {simbolo}")

    meta = results[0].get("meta") or {}
    preco = meta.get("regularMarketPrice")
    if preco is None:
        # fallback: último close da série
        indicators = (results[0].get("indicators") or {}).get("quote") or []
        closes = (indicators[0].get("close") if indicators else None) or []
        closes = [c for c in closes if c is not None]
        if not closes:
            raise ValueError(f"Preço indisponível para {simbolo}")
        preco = closes[-1]

    horario: datetime | None = None
    ts = meta.get("regularMarketTime")
    if ts:
        horario = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(_B3_TZ)

    return Cotacao(
        ticker=ticker.strip().upper().removesuffix(".SA"),
        preco=float(preco),
        horario=horario,
        moeda=str(meta.get("currency") or "BRL"),
        fonte="Yahoo Finance",
    )
