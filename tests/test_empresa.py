"""Testes do perfil da empresa (sem rede)."""

from __future__ import annotations

from calculadora.empresa import _encurtar, fetch_empresa


def test_encurtar_curto():
    assert _encurtar("Texto curto.") == "Texto curto."


def test_encurtar_frase():
    longo = (
        "Primeira frase completa sobre a empresa. "
        "Segunda frase continua o assunto com mais detalhes e contexto. "
        "Terceira frase já pode ser cortada se passar do limite de caracteres definidos."
    )
    curto = _encurtar(longo, limite=120)
    assert len(curto) <= 130
    assert curto.endswith(".") or curto.endswith("…")


def test_exemplo_local():
    info = fetch_empresa("EXEMPLO")
    assert info.nome
    assert "fict" in info.descricao.lower()
