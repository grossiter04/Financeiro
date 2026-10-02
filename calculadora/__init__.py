"""Calculadora de preço teto por proventos."""

from .core import (
    BaseMethod,
    YearCriterion,
    calculate,
    parse_proventos,
)

__all__ = [
    "BaseMethod",
    "YearCriterion",
    "calculate",
    "parse_proventos",
]
