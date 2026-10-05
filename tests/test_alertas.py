"""Testes do vigilante de alertas (sem rede)."""

from __future__ import annotations

from pathlib import Path

from calculadora.alertas import (
    Sinal,
    filtrar_novos,
    formatar_mensagem,
    load_watchlist,
)


def test_load_watchlist(tmp_path: Path):
    cfg = tmp_path / "w.toml"
    cfg.write_text(
        """
dy_padrao = 6.0
usar_banco_local = false
avisar_bazin = true
avisar_graham = false
avisar_dy = true

[[acao]]
ticker = "taee11"
preco_maximo = 35.5

[[acao]]
ticker = "B3SA3"
avisar_graham = true
""",
        encoding="utf-8",
    )
    items = load_watchlist(cfg)
    assert [i.ticker for i in items] == ["B3SA3", "TAEE11"]
    taee = next(i for i in items if i.ticker == "TAEE11")
    assert taee.preco_maximo == 35.5
    assert taee.avisar_graham is False
    b3 = next(i for i in items if i.ticker == "B3SA3")
    assert b3.avisar_graham is True


def test_filtrar_novos_anti_spam():
    state: dict = {"ativos": {}}
    s1 = Sinal("X", "bazin", 10.0, 12.0, -0.1, "ok")
    novos, rep = filtrar_novos([s1], state, registrar=True)
    assert len(novos) == 1 and not rep

    novos2, rep2 = filtrar_novos([s1], state, registrar=True)
    assert not novos2 and len(rep2) == 1

    # Saiu do barato → limpa; depois volta → avisa de novo
    filtrar_novos([], state, registrar=False)
    assert "X|bazin" not in state["ativos"]
    novos3, _ = filtrar_novos([s1], state, registrar=True)
    assert len(novos3) == 1


def test_formatar_mensagem():
    msg = formatar_mensagem(
        [
            Sinal("TAEE11", "bazin", 30.0, 33.0, -0.09, "Barata"),
            Sinal("TAEE11", "graham", 30.0, 40.0, -0.25, "Barata"),
        ]
    )
    assert "TAEE11" in msg
    assert "BAZIN" in msg
    assert "GRAHAM" in msg
