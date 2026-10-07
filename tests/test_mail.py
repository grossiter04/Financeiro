import os

from calculadora.mail import apply_runtime_secrets, smtp_config_from_env


def test_smtp_config_from_env(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert smtp_config_from_env() is None
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "a@b.com")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setenv("ALERT_EMAIL_TO", "a@b.com")
    cfg = smtp_config_from_env()
    assert cfg is not None
    assert cfg["host"] == "smtp.gmail.com"
    assert cfg["port"] == 587


def test_apply_runtime_secrets_nao_sobrescreve(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "ja.setado")
    apply_runtime_secrets({"SMTP_HOST": "novo", "GEMINI_API_KEY": "abc"})
    assert os.environ["SMTP_HOST"] == "ja.setado"
    assert os.environ["GEMINI_API_KEY"] == "abc"
