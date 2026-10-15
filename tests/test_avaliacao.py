"""Testes do motor unificado de avaliação."""

from __future__ import annotations

from calculadora.avaliacao import montar_avaliacao
from calculadora.fundamentos import Fundamentos

_PROV = """\
Dividendo	15/03/2025	30/03/2025	1,00
Dividendo	15/03/2024	30/03/2024	0,90
Dividendo	15/03/2023	30/03/2023	0,80
"""


def test_avaliacao_barata_qualidade_ok():
    av = montar_avaliacao(
        ticker="ITUB4",
        preco=10.0,
        proventos=_PROV,
        pct_bazin=-0.20,
        pct_graham=-0.10,
        selic=0.10,
        ipca_mais=0.06,
        pl=8.0,
        fund=Fundamentos(
            ticker="ITUB4",
            lpa=2.0,
            vpa=12.0,
            fonte="teste",
            pl=8.0,
            payout=0.5,
            roe=0.18,
            fcf=1e9,
            ev=5e9,
            divida=5e8,
        ),
        setor="bancos",
    )
    assert av.decisao == "compra"
    assert av.earnings_yield is not None
    assert any("Bazin" in m or "DY" in m or "ROE" in m for m in av.a_favor)


def test_avaliacao_armadilha_qualidade_fraca():
    """Barata no Bazin mas ROE destruindo valor → não compra."""
    av = montar_avaliacao(
        ticker="TRAP3",
        preco=10.0,
        proventos=_PROV,
        pct_bazin=-0.25,
        pct_graham=-0.20,
        selic=0.12,
        ipca_mais=0.06,
        pl=5.0,
        fund=Fundamentos(
            ticker="TRAP3",
            lpa=1.0,
            vpa=20.0,
            fonte="teste",
            pl=5.0,
            payout=1.5,
            roe=0.02,
            fcf=-1e8,
            ev=1e9,
            divida=8e8,
        ),
        setor="industria",
    )
    assert av.decisao != "compra"
    assert av.score_qualidade is not None and av.score_qualidade < 0
    assert any("armadilha" in m.lower() or "qualidade" in m.lower() or "ROE" in m for m in av.contra)


def test_avaliacao_noticias_negativas():
    av = montar_avaliacao(
        ticker="X",
        preco=10.0,
        proventos=_PROV,
        pct_bazin=-0.18,
        pct_graham=-0.18,
        selic=0.10,
        ipca_mais=0.06,
        sentimento="negativo",
        usar_sentimento=True,
    )
    assert any("notícia" in m.lower() or "Notícia" in m for m in av.contra)
    assert av.sentimento == "negativo"


def test_avaliacao_so_margens_sem_proventos():
    av = montar_avaliacao(
        ticker="Y",
        preco=10.0,
        pct_bazin=-0.20,
        pct_graham=-0.18,
    )
    assert av.decisao == "compra"
    assert av.score >= 1.0
