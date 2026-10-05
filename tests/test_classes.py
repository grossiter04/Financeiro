"""Testes de classes ON/PN/Unit (sem rede)."""

from __future__ import annotations

from calculadora.classes import (
    ClasseComparativo,
    analisar_preco_unit,
    candidatos_classes,
    melhor_por_margem,
    raiz_ticker,
    sufixo_ticker,
    tipo_classe,
)


def test_raiz_e_sufixo():
    assert raiz_ticker("ITUB4") == "ITUB"
    assert raiz_ticker("TAEE11") == "TAEE"
    assert sufixo_ticker("ITUB4") == "4"
    assert sufixo_ticker("TAEE11") == "11"


def test_tipo_classe():
    assert tipo_classe("PETR3") == "ON"
    assert tipo_classe("PETR4") == "PN"
    assert tipo_classe("SANB11") == "Unit"


def test_candidatos():
    cands = candidatos_classes("taee4")
    assert "TAEE3" in cands
    assert "TAEE4" in cands
    assert "TAEE11" in cands


def _linha(ticker: str, preco: float, **kwargs) -> ClasseComparativo:
    defaults = dict(
        tipo=tipo_classe(ticker),
        preco=preco,
        base=1.0,
        dy_atual=0.06,
        teto_usuario=20.0,
        pct_usuario=-0.05,
        teto_bazin=18.0,
        pct_bazin=-0.1,
        veredito_bazin="Barata",
        teto_graham=22.0,
        pct_graham=-0.08,
        veredito_graham="Barata",
        avisos=[],
    )
    defaults.update(kwargs)
    return ClasseComparativo(ticker=ticker, **defaults)


def test_melhor_por_margem():
    linhas = [
        _linha("TAEE3", 10.0, pct_bazin=-0.05),
        _linha("TAEE4", 9.0, pct_bazin=-0.12),
        _linha("TAEE11", 30.0, pct_bazin=-0.02),
    ]
    melhor = melhor_por_margem(linhas, "pct_bazin")
    assert melhor is not None
    assert melhor.ticker == "TAEE4"


def test_analisar_preco_unit_monta(monkeypatch):
    from calculadora.classes import ComponenteUnit, fetch_composicao_unit

    def fake_fetch(_ticker: str, *, timeout: float = 20.0):
        return [
            ComponenteUnit(quantidade=1, tipo="ON", ticker="SANB3"),
            ComponenteUnit(quantidade=1, tipo="PN", ticker="SANB4"),
        ]

    monkeypatch.setattr("calculadora.classes.fetch_composicao_unit", fake_fetch)

    linhas = [
        _linha("SANB3", 10.0),
        _linha("SANB4", 8.0),
        _linha("SANB11", 17.0, pct_bazin=-0.01),
    ]
    analise = analisar_preco_unit(linhas)
    assert analise is not None
    assert analise.soma_componentes == 18.0
    assert analise.preco_unit == 17.0
    assert analise.diff_reais == -1.0
    assert analise.pct_diff < 0


def test_analisar_preco_unit_sem_composicao(monkeypatch):
    monkeypatch.setattr(
        "calculadora.classes.fetch_composicao_unit",
        lambda *_a, **_k: None,
    )
    linhas = [_linha("PETR4", 30.0)]
    assert analisar_preco_unit(linhas) is None
