"""Testes da carteira com posições."""

from __future__ import annotations

from pathlib import Path

import pytest

from datetime import date

from calculadora.carteira import (
    Posicao,
    aplicar_compra,
    aplicar_venda,
    calendario_proventos_acao,
    calendario_proventos_carteira,
    enriquecer_posicoes,
    estimar_proventos_acao,
    estimar_proventos_carteira,
    export_csv_carteira,
    garantir_acoes_das_posicoes,
    parse_csv_carteira,
    posicoes_de_editor,
    preco_medio_apos_compra,
)
from calculadora.storage import (
    listar_posicoes,
    obter_acao,
    salvar_acao,
    salvar_posicao,
    substituir_posicoes,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "acoes.db"


def test_parse_csv_virgula():
    texto = "ticker,quantidade,preco_medio\nITUB4,100,32.5\nPETR4,50,38.1\n"
    pos = parse_csv_carteira(texto)
    assert len(pos) == 2
    assert pos[0].ticker == "ITUB4"
    assert pos[0].quantidade == pytest.approx(100)
    assert pos[0].preco_medio == pytest.approx(32.5)


def test_parse_csv_ponto_e_virgula_decimal_br():
    texto = "ticker;quantidade;preco_medio\nTAEE11;200;35,40\n"
    pos = parse_csv_carteira(texto)
    assert len(pos) == 1
    assert pos[0].ticker == "TAEE11"
    assert pos[0].quantidade == pytest.approx(200)
    assert pos[0].preco_medio == pytest.approx(35.40)


def test_parse_csv_alias_e_milhar_br():
    texto = "ativo;qtd;pm\nBBAS3;1.000;28,50\n"
    pos = parse_csv_carteira(texto)
    assert pos[0].quantidade == pytest.approx(1000)
    assert pos[0].preco_medio == pytest.approx(28.5)


def test_export_csv_roundtrip():
    pos = [
        Posicao("ITUB4", 10, 30.5),
        Posicao("VALE3", 5, 60),
    ]
    texto = export_csv_carteira(pos)
    de_volta = parse_csv_carteira(texto)
    assert [p.ticker for p in de_volta] == ["ITUB4", "VALE3"]
    assert de_volta[0].preco_medio == pytest.approx(30.5)


def test_enriquecer_peso_e_resultado():
    pos = [
        Posicao("A", 10, 10.0),  # custo 100; mercado 150 → peso 0.6
        Posicao("B", 5, 20.0),  # custo 100; mercado 100 → peso 0.4
    ]
    enriq = enriquecer_posicoes(pos, {"A": 15.0, "B": 20.0})
    por = {e.ticker: e for e in enriq}
    assert por["A"].valor_mercado == pytest.approx(150)
    assert por["A"].peso_pct == pytest.approx(0.6)
    assert por["A"].resultado_pct == pytest.approx(0.5)
    assert por["B"].peso_pct == pytest.approx(0.4)
    assert por["B"].resultado_pct == pytest.approx(0.0)


def test_enriquecer_sem_preco():
    pos = [Posicao("X", 1, 10.0)]
    enriq = enriquecer_posicoes(pos, {"X": None})
    assert enriq[0].valor_mercado is None
    assert enriq[0].peso_pct is None
    assert enriq[0].resultado_pct is None


def test_posicoes_de_editor():
    rows = [
        {"ticker": "itub4", "quantidade": 10, "preco_medio": "32,5"},
        {"ticker": "", "quantidade": None, "preco_medio": None},
    ]
    pos = posicoes_de_editor(rows)
    assert len(pos) == 1
    assert pos[0].ticker == "ITUB4"
    assert pos[0].preco_medio == pytest.approx(32.5)


def test_preco_medio_apos_compra():
    # 10 @ 10 + 10 @ 20 = 20 @ 15
    qtd, pm = preco_medio_apos_compra(10, 10.0, 10, 20.0)
    assert qtd == pytest.approx(20)
    assert pm == pytest.approx(15.0)


def test_aplicar_compra_e_venda(db: Path):
    p1 = aplicar_compra(ticker="cmig4", quantidade=10, preco=10.0, db_path=db)
    assert p1.ticker == "CMIG4"
    assert p1.quantidade == pytest.approx(10)
    assert p1.preco_medio == pytest.approx(10.0)

    p2 = aplicar_compra(ticker="CMIG4", quantidade=10, preco=20.0, db_path=db)
    assert p2.quantidade == pytest.approx(20)
    assert p2.preco_medio == pytest.approx(15.0)

    p3 = aplicar_venda(ticker="CMIG4", quantidade=5, db_path=db)
    assert p3 is not None
    assert p3.quantidade == pytest.approx(15)
    assert p3.preco_medio == pytest.approx(15.0)

    assert aplicar_venda(ticker="CMIG4", quantidade=15, db_path=db) is None
    assert listar_posicoes(db_path=db) == []


def test_crud_posicoes(db: Path):
    salvar_posicao(ticker="ITUB4", quantidade=100, preco_medio=30.0, db_path=db)
    salvar_posicao(ticker="PETR4", quantidade=50, preco_medio=40.0, db_path=db)
    todas = listar_posicoes(db_path=db)
    assert len(todas) == 2
    substituir_posicoes([("VALE3", 20, 55.0)], db_path=db)
    todas = listar_posicoes(db_path=db)
    assert len(todas) == 1
    assert todas[0].ticker == "VALE3"


def test_garantir_acoes_das_posicoes(monkeypatch, db: Path):
    class Cot:
        preco = 33.0

    class Prov:
        texto = "Dividendo\t01/01/2025\t01/02/2025\t0,10"

    monkeypatch.setattr("calculadora.carteira.fetch_preco", lambda t: Cot())
    monkeypatch.setattr("calculadora.carteira.fetch_proventos", lambda t: Prov())

    pos = [Posicao("ITUB4", 10, 30.0)]
    avisos = garantir_acoes_das_posicoes(pos, db_path=db)
    assert any("cadastrado" in a for a in avisos)
    acao = obter_acao("ITUB4", db_path=db)
    assert acao is not None
    assert acao.preco == pytest.approx(33.0)
    assert "Dividendo" in acao.proventos

    # segunda vez não recria
    avisos2 = garantir_acoes_das_posicoes(pos, db_path=db)
    assert not any("cadastrado" in a for a in avisos2)


_PROVENTOS_EST = """\
Dividendo	15/03/2025	30/03/2025	1,00
Dividendo	15/03/2024	30/03/2024	0,80
Dividendo	15/03/2023	30/03/2023	0,60
"""


def test_estimar_proventos_acao():
    est = estimar_proventos_acao(
        ticker="ITUB4",
        quantidade=100,
        proventos_texto=_PROVENTOS_EST,
        n_anos=3,
    )
    assert est.anos_usados == (2023, 2024, 2025)
    assert est.por_acao_medio == pytest.approx(0.8)
    assert est.por_acao_min == pytest.approx(0.6)
    assert est.por_acao_max == pytest.approx(1.0)
    assert est.total_medio == pytest.approx(80.0)
    assert est.total_min == pytest.approx(60.0)
    assert est.total_max == pytest.approx(100.0)
    assert est.total_ultimo == pytest.approx(100.0)
    assert est.ano_ultimo == 2025


def test_estimar_proventos_acao_sem_dados():
    est = estimar_proventos_acao(
        ticker="XYZ", quantidade=10, proventos_texto="", n_anos=3
    )
    assert est.total_medio is None
    assert est.aviso


def test_estimar_proventos_carteira(db: Path):
    salvar_acao(
        ticker="ITUB4",
        preco=30.0,
        dy=6.0,
        proventos=_PROVENTOS_EST,
        db_path=db,
    )
    salvar_acao(
        ticker="TAEE11",
        preco=35.0,
        dy=6.0,
        proventos="Dividendo\t10/05/2025\t20/05/2025\t2,00\n"
        "Dividendo\t10/05/2024\t20/05/2024\t2,00\n",
        db_path=db,
    )
    pos = [
        Posicao("ITUB4", 100, 30.0),
        Posicao("TAEE11", 50, 35.0),
    ]
    cart = estimar_proventos_carteira(pos, n_anos=2, db_path=db)
    # ITUB4: média (0.8+1.0)/2=0.9 → 90; TAEE11: 2.0 → 100
    assert cart.total_medio == pytest.approx(190.0)
    assert cart.mensal_medio == pytest.approx(190.0 / 12.0)
    assert cart.total_min == pytest.approx(80.0 + 100.0)  # 0.8*100 + 2*50
    assert cart.total_max == pytest.approx(100.0 + 100.0)


_CALENDARIO = """\
Dividendo	10/03/2027	20/03/2027	0,50
Dividendo	10/03/2025	20/03/2025	0,40
Dividendo	10/03/2024	20/03/2024	0,40
Dividendo	10/08/2025	20/08/2025	0,30
Dividendo	10/08/2024	20/08/2024	0,30
Dividendo	10/11/2023	20/11/2023	0,10
"""


def test_calendario_oficial_e_estimado():
    cal = calendario_proventos_acao(
        ticker="CMIG4",
        proventos_texto=_CALENDARIO,
        hoje=date(2026, 10, 8),
    )
    assert cal.meses_oficiais == (3,)
    assert cal.oficiais[0].data == date(2027, 3, 20)
    # Mar e Ago em ≥2 anos; Nov só 1 ano → fora
    assert 3 not in cal.meses_estimados  # já oficial
    assert 8 in cal.meses_estimados
    assert 11 not in cal.meses_estimados
    assert cal.marca_mes(3) == "●"
    assert cal.marca_mes(8) == "○"
    assert cal.marca_mes(1) == ""


def test_calendario_carteira(db: Path):
    salvar_acao(
        ticker="CMIG4", preco=11.0, dy=6.0, proventos=_CALENDARIO, db_path=db
    )
    cart = calendario_proventos_carteira(
        [Posicao("CMIG4", 10, 11.0)],
        db_path=db,
        hoje=date(2026, 10, 8),
    )
    assert cart.por_acao[0].ticker == "CMIG4"
    meses = {m for m, _ in cart.por_mes}
    assert 3 in meses and 8 in meses
