"""Envio de e-mail e leitura de secrets (Streamlit / variáveis de ambiente)."""

from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")
_BRT = BRT  # compat interno

_SECRET_ENV_KEYS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "ALERT_EMAIL_TO",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
)


def apply_runtime_secrets(secrets: object | None = None) -> None:
    """Copia chaves de st.secrets (ou dict) para os.environ só se ainda estiverem vazias."""
    if secrets is None:
        return
    for key in _SECRET_ENV_KEYS:
        if os.environ.get(key, "").strip():
            continue
        try:
            val = secrets[key]  # type: ignore[index]
        except Exception:  # noqa: BLE001
            continue
        if val is None:
            continue
        texto = str(val).strip()
        if texto:
            os.environ[key] = texto


def smtp_config_from_env() -> dict[str, str | int] | None:
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    para = os.environ.get("ALERT_EMAIL_TO", "").strip() or user
    if not (host and user and password and para):
        return None
    port_raw = (os.environ.get("SMTP_PORT") or "587").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 587
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "para": para,
        "de": (os.environ.get("SMTP_FROM") or user).strip(),
    }


def enviar_email(
    texto: str,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    para: str,
    de: str | None = None,
    assunto: str = "Relatório Financeiro",
    html: str | None = None,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = de or user
    msg["To"] = para
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)


def agora_brt() -> datetime:
    return datetime.now(BRT)
