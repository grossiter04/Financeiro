"""Testes da cotação e helpers (sem rede)."""

from __future__ import annotations

import pytest

from calculadora.preco import RANGES_HISTORICO, _simbolo_yahoo, logo_url


def test_simbolo_yahoo():
    assert _simbolo_yahoo("petr4") == "PETR4.SA"
    assert _simbolo_yahoo("ITUB4.SA") == "ITUB4.SA"


def test_simbolo_vazio():
    with pytest.raises(ValueError):
        _simbolo_yahoo("  ")


def test_logo_url():
    assert logo_url("itub4") == "https://icons.brapi.dev/icons/ITUB4.svg"
    assert logo_url("EXEMPLO") == ""
    assert logo_url("") == ""


def test_ranges_historico():
    assert "1y" in RANGES_HISTORICO
