"""Cotação e histórico via Yahoo Finance (B3: TICKER.SA)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

_B3_TZ = ZoneInfo("America/Sao_Paulo")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Intervalo Yahoo → rótulo na UI
RANGES_HISTORICO: dict[str, str] = {
    "1mo": "1 mês",
    "3mo": "3 meses",
    "6mo": "6 meses",
    "1y": "1 ano",
    "2y": "2 anos",
    "5y": "5 anos",
}


@dataclass
class Cotacao:
    ticker: str
    preco: float
    horario: datetime | None
    moeda: str
    fonte: str


@dataclass
class PontoHistorico:
    data: date
    preco: float


@dataclass
class Historico:
    ticker: str
    pontos: list[PontoHistorico]
    periodo: str
    fonte: str


def _simbolo_yahoo(ticker: str) -> str:
    t = ticker.strip().upper()
    if not t:
        raise ValueError("ticker vazio")
    if t.endswith(".SA"):
        return t
    return f"{t}.SA"


def logo_url(ticker: str) -> str:
    """URL previsível do ícone (brapi). Pode 404 se o ticker não existir."""
    t = ticker.strip().upper().removesuffix(".SA")
    if not t or t == "EXEMPLO":
        return ""
    return f"https://icons.brapi.dev/icons/{t}.svg"


def fetch_preco(ticker: str, *, timeout: float = 15.0) -> Cotacao:
    """
    Busca o preço mais recente no Yahoo Finance.
    Em pregão costuma atualizar com atraso baixo (segundos a poucos minutos).
    Fora do pregão, devolve o último negócio do dia.
    """
    simbolo = _simbolo_yahoo(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}"
    params = {"interval": "1m", "range": "1d"}

    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        resp = client.get(url, params=params)
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


def fetch_historico(
    ticker: str,
    *,
    periodo: str = "1y",
    timeout: float = 20.0,
) -> Historico:
    """
    Série diária de fechamento para o gráfico de evolução.
    periodo: chave de RANGES_HISTORICO (ex.: 1mo, 3mo, 1y).
    """
    periodo = (periodo or "1y").strip()
    if periodo not in RANGES_HISTORICO:
        raise ValueError(f"período inválido: {periodo!r}")

    simbolo = _simbolo_yahoo(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}"
    params = {"interval": "1d", "range": periodo}

    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()

    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"Yahoo Finance: {chart['error']}")

    results = chart.get("result") or []
    if not results:
        raise ValueError(f"Sem histórico para {simbolo}")

    bloco = results[0]
    timestamps = bloco.get("timestamp") or []
    indicators = (bloco.get("indicators") or {}).get("quote") or []
    closes = (indicators[0].get("close") if indicators else None) or []

    pontos: list[PontoHistorico] = []
    for ts, close in zip(timestamps, closes):
        if close is None or ts is None:
            continue
        dia = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(_B3_TZ).date()
        pontos.append(PontoHistorico(data=dia, preco=float(close)))

    if not pontos:
        raise ValueError(f"Histórico vazio para {simbolo}")

    return Historico(
        ticker=ticker.strip().upper().removesuffix(".SA"),
        pontos=pontos,
        periodo=periodo,
        fonte="Yahoo Finance",
    )
