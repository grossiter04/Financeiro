"""Testes da persistência SQLite."""

from __future__ import annotations

from pathlib import Path

import pytest

from calculadora.storage import (
    excluir_acao,
    listar_acoes,
    obter_acao,
    resolve_db_path,
    salvar_acao,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "acoes.db"


def test_resolve_db_path_explicit(tmp_path: Path):
    alvo = tmp_path / "x.db"
    assert resolve_db_path(alvo) == alvo


def test_resolve_db_path_env_db_path(monkeypatch, tmp_path: Path):
    alvo = tmp_path / "custom.db"
    monkeypatch.setenv("DB_PATH", str(alvo))
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert resolve_db_path() == alvo


def test_resolve_db_path_env_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert resolve_db_path() == tmp_path / "acoes.db"


def test_salvar_e_obter(db: Path):
    salva = salvar_acao(
        ticker="itub4",
        preco=40.5,
        dy=6.0,
        proventos="Dividendo\t01/01/2025\t01/02/2025\t0,10",
        db_path=db,
    )
    assert salva.ticker == "ITUB4"
    obtida = obter_acao("ITUB4", db_path=db)
    assert obtida is not None
    assert obtida.preco == pytest.approx(40.5)
    assert obtida.dy == pytest.approx(6.0)
    assert "Dividendo" in obtida.proventos


def test_atualizar_mesma_acao(db: Path):
    salvar_acao(ticker="PETR4", preco=30.0, dy=8.0, proventos="a", db_path=db)
    salvar_acao(
        ticker="PETR4",
        preco=31.5,
        dy=7.5,
        proventos="a\nJCP\t01/06/2025\t01/07/2025\t0,20",
        db_path=db,
    )
    todas = listar_acoes(db_path=db)
    assert len(todas) == 1
    assert todas[0].preco == pytest.approx(31.5)
    assert "JCP" in todas[0].proventos


def test_excluir(db: Path):
    salvar_acao(ticker="VALE3", preco=50.0, dy=5.0, proventos="x", db_path=db)
    assert excluir_acao("VALE3", db_path=db) is True
    assert obter_acao("VALE3", db_path=db) is None


def test_salvar_sem_ticker_falha(db: Path):
    with pytest.raises(ValueError):
        salvar_acao(ticker="  ", preco=10.0, dy=6.0, proventos="", db_path=db)
