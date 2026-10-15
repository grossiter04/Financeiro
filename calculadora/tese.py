"""Tese de compra: wrapper da avaliação unificada (compat UI)."""

from __future__ import annotations

from dataclasses import dataclass, field

from calculadora.avaliacao import (
    DECISOES,
    IPCA_MAIS_PADRAO,
    SELIC_PADRAO,
    Avaliacao,
    Motivo,
    montar_avaliacao,
)
from calculadora.fundamentos import Fundamentos

# Reexport para imports existentes
__all__ = [
    "SELIC_PADRAO",
    "IPCA_MAIS_PADRAO",
    "DECISOES",
    "Motivo",
    "TeseDividendo",
    "montar_tese_dividendo",
]


@dataclass
class TeseDividendo:
    """Compatível com a UI antiga; espelha ``Avaliacao``."""

    decisao: str  # compra | aguardar | cautela | sem_dados
    rotulo: str
    score: float
    resumo: str
    motivos: list[Motivo] = field(default_factory=list)
    dy_estimado: float | None = None
    earnings_yield: float | None = None
    fcf_yield: float | None = None
    ev_ebit: float | None = None
    g_implicito: float | None = None
    detalhe: str = (
        "Indicativo — não é recomendação de investimento. "
        "Combina qualidade, múltiplos, juro real e notícias diretas."
    )

    @property
    def cor(self) -> str:
        from calculadora.avaliacao import _CORES

        return _CORES.get(self.decisao, _CORES["sem_dados"])

    @property
    def a_favor(self) -> list[str]:
        return [m.texto for m in self.motivos if m.sentido == "a_favor"]

    @property
    def contra(self) -> list[str]:
        return [m.texto for m in self.motivos if m.sentido == "contra"]

    @classmethod
    def de_avaliacao(cls, av: Avaliacao) -> TeseDividendo:
        return cls(
            decisao=av.decisao,
            rotulo=av.rotulo,
            score=av.score,
            resumo=av.resumo,
            motivos=list(av.motivos),
            dy_estimado=av.dy_estimado,
            earnings_yield=av.earnings_yield,
            fcf_yield=av.fcf_yield,
            ev_ebit=av.ev_ebit,
            g_implicito=av.g_implicito,
            detalhe=av.detalhe,
        )


def montar_tese_dividendo(
    *,
    ticker: str,
    preco: float,
    proventos: str,
    pct_bazin: float | None = None,
    pct_graham: float | None = None,
    selic: float = SELIC_PADRAO,
    ipca_mais: float = IPCA_MAIS_PADRAO,
    pl: float | None = None,
    payout: float | None = None,
    peso_setor_carteira: float | None = None,
    setor: str = "",
    n_anos_dy: int = 3,
    fund: Fundamentos | None = None,
    sentimento: str | None = None,
    usar_sentimento: bool = False,
    g_implicito: float | None = None,
    g_historico: float | None = None,
) -> TeseDividendo:
    """
    Monta decisão Comprar/Esperar/Evitar via motor unificado ``montar_avaliacao``.
    """
    av = montar_avaliacao(
        ticker=ticker,
        preco=preco,
        proventos=proventos,
        pct_bazin=pct_bazin,
        pct_graham=pct_graham,
        selic=selic,
        ipca_mais=ipca_mais,
        pl=pl,
        payout=payout,
        fund=fund,
        setor=setor,
        peso_setor_carteira=peso_setor_carteira,
        n_anos_dy=n_anos_dy,
        sentimento=sentimento,
        usar_sentimento=usar_sentimento,
        g_implicito=g_implicito,
        g_historico=g_historico,
    )
    return TeseDividendo.de_avaliacao(av)
