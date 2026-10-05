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
    logos: list[str] | None = None,
    active: int = 0,
    key: str | None = None,
) -> dict[str, Any] | None:
    """
    Renderiza abas compactas. Retorna {"action": "select"|"close", "index": int}
    quando o usuário clica; None no carregamento inicial.
    """
    n = len(labels)
    logos_ok = list(logos or [])
    while len(logos_ok) < n:
        logos_ok.append("")
    return _component(
        labels=labels,
        logos=logos_ok[:n],
        active=int(active),
        key=key,
        default=None,
    )
