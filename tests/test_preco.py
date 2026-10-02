"""Testes da cotação (sem rede, só formatação)."""

from __future__ import annotations

import pytest

from calculadora.preco import _simbolo_yahoo


def test_simbolo_yahoo():
    assert _simbolo_yahoo("petr4") == "PETR4.SA"
    assert _simbolo_yahoo("ITUB4.SA") == "ITUB4.SA"


def test_simbolo_vazio():
    with pytest.raises(ValueError):
        _simbolo_yahoo("  ")
