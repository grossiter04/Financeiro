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
    s = (setor or "").strip().lower()
    if not s:
        return "outros"
    if s in ROTULOS_SETOR:
        return s
    # Yahoo/brapi em inglês (ex.: Industrials) → chave local
    return setor_de_perfil(s)


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


# Yahoo / brapi (EN e PT) → chave interna
_SETOR_EXTERNO: dict[str, str] = {
    "industrials": "industria",
    "industrial": "industria",
    "indústria": "industria",
    "industria": "industria",
    "financial services": "bancos",
    "financials": "bancos",
    "banks": "bancos",
    "bancos": "bancos",
    "utilities": "energia",
    "utility": "energia",
    "energia": "energia",
    "energy": "petroleo",
    "oil & gas": "petroleo",
    "petroleo": "petroleo",
    "petróleo": "petroleo",
    "basic materials": "mineracao",
    "basicmaterials": "mineracao",
    "mineracao": "mineracao",
    "mineração": "mineracao",
    "communication services": "telecom",
    "communications": "telecom",
    "telecom": "telecom",
    "telecommunications": "telecom",
    "healthcare": "saude",
    "health care": "saude",
    "saude": "saude",
    "saúde": "saude",
    "consumer cyclical": "varejo",
    "consumer defensive": "varejo",
    "consumer staples": "varejo",
    "consumer discretionary": "varejo",
    "varejo": "varejo",
    "real estate": "construcao",
    "construcao": "construcao",
    "construção": "construcao",
    "technology": "industria",
    "agriculture": "agro",
    "agro": "agro",
}


def setor_de_perfil(setor: str | None = None, industria: str | None = None) -> str:
    """
    Converte setor/indústria externos (Yahoo, brapi) para a chave local.

    Indústria tem prioridade quando é mais específica (ex.: Insurance → seguros).
    """
    ind = (industria or "").strip().lower()
    sec = (setor or "").strip().lower()
    blob = f"{sec} {ind}"

    if any(k in blob for k in ("insurance", "seguro", "seguridade")):
        return "seguros"
    if any(k in blob for k in ("bank", "banco", "financial services")):
        return "bancos"
    if "exchange" in blob or "bolsa" in blob:
        return "bolsa"
    if any(k in blob for k in ("oil", "gas", "petróleo", "petroleo", "refin")):
        return "petroleo"
    if any(k in blob for k in ("mining", "miner", "steel", "siderurg")):
        return "mineracao"
    if any(k in blob for k in ("paper", "pulp", "celulose", "papel")):
        return "papel"
    if any(k in blob for k in ("telecom", "wireless", "comunicação", "comunicacao")):
        return "telecom"
    if any(k in blob for k in ("water", "saneamento", "utilities-regulated water")):
        return "saneamento"
    if any(k in blob for k in ("electric", "utilities", "energia", "transmission")):
        return "energia"
    if any(k in blob for k in ("health", "pharma", "hospital", "saúde", "saude")):
        return "saude"
    if any(k in blob for k in ("retail", "store", "varejo", "e-commerce")):
        return "varejo"
    if any(k in blob for k in ("real estate", "reit", "construção", "construcao", "builder")):
        return "construcao"
    if any(k in blob for k in ("farm", "agro", "agriculture", "fertiliz")):
        # Marcopolo = Farm & Heavy Construction Machinery → indústria, não agro
        if "machinery" in blob or "construction" in blob or "bus" in blob:
            return "industria"
        return "agro"
    if any(k in blob for k in ("industrial", "indústria", "industria", "machinery", "aerospace")):
        return "industria"

    if sec in _SETOR_EXTERNO:
        return _SETOR_EXTERNO[sec]
    if sec in ROTULOS_SETOR:
        return sec
    return "outros"


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
