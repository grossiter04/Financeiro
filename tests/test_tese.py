"""Testes da tese de dividendos (wrapper da avaliação unificada)."""

from __future__ import annotations

from calculadora.tese import montar_tese_dividendo

_PROV = """\
Dividendo	15/03/2025	30/03/2025	1,00
Dividendo	15/03/2024	30/03/2024	0,90
Dividendo	15/03/2023	30/03/2023	0,80
"""


def test_tese_comprar_barata_bom_dy():
    # base ~0.9/ação → DY alto se preço 10
    tese = montar_tese_dividendo(
        ticker="ITUB4",
        preco=10.0,
        proventos=_PROV,
        pct_bazin=-0.20,
        pct_graham=-0.10,
        selic=0.10,
        ipca_mais=0.06,
    )
    assert tese.decisao == "compra"
    assert tese.rotulo == "Comprar"
    assert tese.a_favor


def test_tese_evitar_cara():
    tese = montar_tese_dividendo(
        ticker="X",
        preco=12.0,
        proventos=_PROV,
        pct_bazin=0.35,
        pct_graham=0.40,
        selic=0.15,
        ipca_mais=0.06,
    )
    assert tese.decisao in ("cautela", "aguardar")
    assert tese.contra


def test_tese_so_proventos_bazin():
    tese = montar_tese_dividendo(
        ticker="Y",
        preco=10.0,
        proventos=_PROV,
        pct_bazin=-0.18,
        selic=0.10,
        ipca_mais=0.06,
    )
    assert tese.decisao in ("compra", "aguardar")
    assert any("Bazin" in m or "DY" in m for m in tese.a_favor + [tese.resumo])
