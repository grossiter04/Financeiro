"""Componente de abas no estilo navegador."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

_component = components.declare_component(
    "browser_tabs",
    path=str(Path(__file__).resolve().parent),
)


def browser_tabs(
    labels: list[str],
    *,
    active: int = 0,
    key: str | None = None,
) -> dict[str, Any] | None:
    """
    Renderiza abas compactas. Retorna {"action": "select"|"close", "index": int}
    quando o usuário clica; None no carregamento inicial.
    """
    return _component(
        labels=labels,
        active=int(active),
        key=key,
        default=None,
    )
