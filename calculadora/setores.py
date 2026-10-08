"""Setores da carteira: rótulos, ordem de exibição e agrupamento."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

from calculadora.noticias import Empresa, load_empresas, raiz_ticker

T = TypeVar("T")

# Perenes / mais usados primeiro; o restante em ordem alfabética no fim.
ORDEM_SETORES: tuple[str, ...] = (
    "bancos",
    "seguros",
    "energia",
    "petroleo",
    "mineracao",
    "papel",
    "bolsa",
    "industria",
    "saneamento",
    "telecom",
    "saude",
    "varejo",
    "construcao",
    "agro",
    "outros",
)

ROTULOS_SETOR: dict[str, str] = {
    "bancos": "Bancos",
    "seguros": "Seguros",
    "energia": "Energia",
    "petroleo": "Petróleo",
    "mineracao": "Mineração",
    "papel": "Papel e celulose",
    "bolsa": "Bolsa",
    "industria": "Indústria",
    "saneamento": "Saneamento",
    "telecom": "Telecom",
    "saude": "Saúde",
    "varejo": "Varejo",
    "construcao": "Construção",
    "agro": "Agro",
    "outros": "Outros",
}


def normalizar_setor(setor: str | None) -> str:
    s = (setor or "outros").strip().lower()
    return s or "outros"


def rotulo_setor(setor: str | None) -> str:
    chave = normalizar_setor(setor)
    return ROTULOS_SETOR.get(chave, chave.replace("_", " ").title())


def ordem_setor(setor: str | None) -> tuple[int, str]:
    chave = normalizar_setor(setor)
    try:
        idx = ORDEM_SETORES.index(chave)
    except ValueError:
        idx = len(ORDEM_SETORES)
    return (idx, chave)


def setor_do_ticker(
    ticker: str,
    empresas: dict[str, Empresa] | None = None,
) -> str:
    emp = (empresas or load_empresas()).get(raiz_ticker(ticker))
    return normalizar_setor(emp.setor if emp else None)


def agrupar_por_setor(
    itens: Sequence[T] | Iterable[T],
    *,
    setor_de: Callable[[T], str],
    chave_item: Callable[[T], str] | None = None,
) -> list[tuple[str, list[T]]]:
    """Agrupa itens por setor (perenes primeiro). Retorna (setor, itens)."""
    buckets: dict[str, list[T]] = defaultdict(list)
    for item in itens:
        buckets[normalizar_setor(setor_de(item))].append(item)

    vistos: set[str] = set()
    out: list[tuple[str, list[T]]] = []
    for setor in ORDEM_SETORES:
        if setor not in buckets:
            continue
        grupo = buckets[setor]
        if chave_item is not None:
            grupo = sorted(grupo, key=chave_item)
        out.append((setor, grupo))
        vistos.add(setor)
    for setor in sorted(k for k in buckets if k not in vistos):
        grupo = buckets[setor]
        if chave_item is not None:
            grupo = sorted(grupo, key=chave_item)
        out.append((setor, grupo))
    return out
