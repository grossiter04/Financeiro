"""Testes do DCF forward e reverse (sem rede)."""

from __future__ import annotations

from calculadora.dcf import calcular_dcf, crescimento_implicito


def test_dcf_forward_basico():
    r = calcular_dcf(
        ticker="TEST3",
        preco_atual=20.0,
        fcf_historico=[80.0, 90.0, 100.0],
        fcf_ttm=100.0,
        divida_total=50.0,
        caixa=20.0,
        acoes=10.0,
        beta=1.0,
        selic=0.10,
        premio_mercado=0.05,
        crescimento=0.05,
        g_terminal=0.03,
        anos=5,
    )
    assert r.disponivel
    assert r.preco_justo is not None and r.preco_justo > 0
    assert r.wacc is not None and r.wacc > 0
    assert r.margem is not None
    assert len(r.projecao) == 5


def test_dcf_sem_fcf():
    r = calcular_dcf(
        ticker="X",
        preco_atual=10.0,
        fcf_historico=[-1.0, -2.0],
        fcf_ttm=-5.0,
        acoes=1.0,
    )
    assert not r.disponivel


def test_reverse_dcf_resolve_g():
    # Monta um preço justo com g conhecido e recupera g implícito
    forward = calcular_dcf(
        ticker="REV3",
        preco_atual=1.0,  # irrelevante para montar justo
        fcf_ttm=100.0,
        divida_total=0.0,
        caixa=0.0,
        acoes=10.0,
        beta=1.0,
        selic=0.10,
        premio_mercado=0.05,
        crescimento=0.06,
        g_terminal=0.03,
        anos=5,
    )
    assert forward.disponivel and forward.preco_justo
    rev = crescimento_implicito(
        preco_atual=forward.preco_justo,
        fcf0=100.0,
        wacc=forward.wacc or 0.15,
        divida_liquida=0.0,
        acoes=10.0,
        g_terminal=0.03,
        anos=5,
    )
    assert rev.disponivel
    assert rev.g_implicito is not None
    assert abs(rev.g_implicito - 0.06) < 0.015
