"""Testes do fetch de proventos (sem rede)."""

from __future__ import annotations

from calculadora.proventos_fetch import _fmt_valor, _label_to_tipo, _linhas_from_rows


def test_label_tipos():
    assert _label_to_tipo("JCP") == "JCP"
    assert _label_to_tipo("Juros Sobre Capital Próprio") == "JCP"
    assert _label_to_tipo("Dividendo") == "Dividendo"
    assert _label_to_tipo("DIVIDENDO") == "Dividendo"
    assert _label_to_tipo("Rendimento") == "Dividendo"
    assert _label_to_tipo("Rend. Tributado") == "Rend. Tributado"
    assert _label_to_tipo("bonificação") is None


def test_fmt_valor():
    assert _fmt_valor(0.01765) == "0,01765"
    assert "," in _fmt_valor(1.868223)


def test_linhas_pagamento_ausente():
    avisos: list[str] = []
    texto = _linhas_from_rows(
        [("JCP", "30/11/2026", "-", 0.01765)],
        avisos,
    )
    assert "JCP\t30/11/2026\t30/11/2026\t0,01765" in texto
