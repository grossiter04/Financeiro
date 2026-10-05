"""Teste do sync watchlist."""

from __future__ import annotations

from pathlib import Path

from calculadora.storage import salvar_acao
from calculadora.sync_watchlist import sync_watchlist


def test_sync_watchlist(tmp_path: Path):
    db = tmp_path / "acoes.db"
    salvar_acao(ticker="ITUB4", preco=30.0, dy=6.0, proventos="x", db_path=db)
    salvar_acao(ticker="PETR4", preco=20.0, dy=8.0, proventos="y", db_path=db)
    out = tmp_path / "watchlist.toml"
    tickers = sync_watchlist(out=out, db_path=db)
    assert tickers == ["ITUB4", "PETR4"]
    text = out.read_text(encoding="utf-8")
    assert 'ticker = "ITUB4"' in text
    assert 'ticker = "PETR4"' in text
    assert "dy_desejado = 8.00" in text
    assert "usar_banco_local = true" in text
