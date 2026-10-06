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


def test_filtrar_novos_anti_spam_diario(monkeypatch):
    from calculadora import alertas as mod

    monkeypatch.setattr(mod, "_hoje_brt", lambda: "2026-10-06")
    state: dict = {"ativos": {}}
    s1 = Sinal("X", "bazin", 10.0, 12.0, -0.1, "ok")

    novos, rep = filtrar_novos([s1], state, registrar=True)
    assert len(novos) == 1 and not rep
    assert state["ativos"]["X|bazin"]["dia"] == "2026-10-06"

    novos2, rep2 = filtrar_novos([s1], state, registrar=True)
    assert not novos2 and len(rep2) == 1

    # Dia seguinte → avisa de novo mesmo ainda barata
    monkeypatch.setattr(mod, "_hoje_brt", lambda: "2026-10-07")
    novos3, rep3 = filtrar_novos([s1], state, registrar=True)
    assert len(novos3) == 1 and not rep3

    # Force no mesmo dia
    novos4, _ = filtrar_novos([s1], state, registrar=True, forcar=True)
    assert len(novos4) == 1


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
    assert "*" not in msg


def test_smtp_config_from_env(monkeypatch):
    from calculadora.alertas import _smtp_config_from_env

    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert _smtp_config_from_env() is None

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "a@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "xxxx")
    monkeypatch.setenv("ALERT_EMAIL_TO", "b@gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    cfg = _smtp_config_from_env()
    assert cfg is not None
    assert cfg["host"] == "smtp.gmail.com"
    assert cfg["para"] == "b@gmail.com"
    assert cfg["port"] == 587
