"""Sinal orientativo: wrapper da avaliação unificada + fila."""

from __future__ import annotations

from dataclasses import dataclass

from calculadora.avaliacao import (
    IPCA_MAIS_PADRAO,
    SELIC_PADRAO,
    montar_avaliacao,
)
from calculadora.core import (
    BAZIN_DY,
    BaseMethod,
    DatasComResumo,
    YearCriterion,
    calculate,
    conta_bazin,
    conta_graham,
    resumo_datas_com,
)
from calculadora.fundamentos import Fundamentos
from calculadora.setores import normalizar_setor

# Código → rótulo curto (fila / badge)
SINAIS = {
    "compra": "Comprar",
    "aguardar": "Esperar",
    "cautela": "Evitar",
    "sem_dados": "Sem dados",
}

_CORES = {
    "compra": "#3f6f5a",
    "aguardar": "#7a6f5d",
    "cautela": "#8a5a5a",
    "sem_dados": "#6b7280",
}

_PCT_MAX_UTIL = 1.50  # ±150%


@dataclass(frozen=True)
class SinalOrientativo:
    codigo: str  # compra | aguardar | cautela | sem_dados
    rotulo: str
    score: float
    resumo: str
    detalhe: str = ""

    @property
    def cor(self) -> str:
        return _CORES.get(self.codigo, _CORES["sem_dados"])


def _pct_util(pct: float | None) -> float | None:
    """Descarta margens absurdas (ex.: +4000%) que distorcem o sinal."""
    if pct is None:
        return None
    if abs(pct) > _PCT_MAX_UTIL:
        return None
    return pct


def _pontos_margem(pct: float | None) -> float | None:
    """Margem vs teto: negativo = barata → pontos positivos. Escala suave."""
    pct = _pct_util(pct)
    if pct is None:
        return None
    if pct <= -0.15:
        return 2.0
    if pct <= -0.05:
        return 1.0
    if pct < 0.05:
        return 0.0
    if pct < 0.20:
        return -1.0
    return -2.0


def formatar_margem_pct(pct: float | None) -> str:
    """Texto curto para UI; evita números absurdos."""
    if pct is None:
        return "—"
    if abs(pct) > _PCT_MAX_UTIL:
        return "muito acima" if pct > 0 else "muito abaixo"
    return f"{pct * 100:+.0f}%"


def calcular_sinal(
    *,
    pct_bazin: float | None = None,
    pct_graham: float | None = None,
    sentimento: str | None = None,
    usar_sentimento: bool = False,
    ticker: str = "—",
    preco: float = 0.0,
    proventos: str = "",
    selic: float = SELIC_PADRAO,
    ipca_mais: float = IPCA_MAIS_PADRAO,
    pl: float | None = None,
    payout: float | None = None,
    fund: Fundamentos | None = None,
    setor: str = "",
    peso_setor_carteira: float | None = None,
) -> SinalOrientativo:
    """
    Combina valuation e (opcionalmente) sentimento via motor unificado.

    Mantém a assinatura clássica (Bazin/Graham ± notícias) e aceita fundamentos
    extras quando disponíveis.
    """
    av = montar_avaliacao(
        ticker=ticker,
        preco=preco if preco > 0 else 1.0,  # evita divisão zero em DY se só margens
        proventos=proventos,
        pct_bazin=pct_bazin,
        pct_graham=pct_graham,
        selic=selic,
        ipca_mais=ipca_mais,
        pl=pl,
        payout=payout,
        fund=fund,
        setor=normalizar_setor(setor) if setor else "",
        peso_setor_carteira=peso_setor_carteira,
        sentimento=sentimento,
        usar_sentimento=usar_sentimento,
    )
    return SinalOrientativo(
        codigo=av.decisao,
        rotulo=av.rotulo,
        score=av.score,
        resumo=av.resumo,
        detalhe=av.detalhe,
    )


def status_curto(sinal: SinalOrientativo) -> str:
    """Texto compacto para a fila do topo."""
    return sinal.rotulo


@dataclass(frozen=True)
class ItemFila:
    ticker: str
    preco: float
    pct_bazin: float | None
    pct_graham: float | None
    sinal: SinalOrientativo
    setor: str = "outros"
    datas_com: DatasComResumo = DatasComResumo()

    @property
    def texto(self) -> str:
        bits = [self.ticker]
        if self.preco > 0:
            bits.append(f"R$ {self.preco:.2f}".replace(".", ","))
        if self.pct_bazin is not None:
            bits.append(f"Bazin {formatar_margem_pct(self.pct_bazin)}")
        if self.pct_graham is not None:
            bits.append(f"Graham {formatar_margem_pct(self.pct_graham)}")
        if self.datas_com.proxima or self.datas_com.ultima:
            bits.append(f"com {self.datas_com.fmt_destaque()}")
        bits.append(self.sinal.rotulo)
        return " · ".join(bits)


def item_fila_de_salva(
    *,
    ticker: str,
    preco: float,
    dy: float,
    proventos: str,
    lpa: float | None = None,
    vpa: float | None = None,
    sentimento: str | None = None,
    setor: str = "outros",
    n_anos: int = 5,
    fund: Fundamentos | None = None,
    selic: float = SELIC_PADRAO,
    ipca_mais: float = IPCA_MAIS_PADRAO,
) -> ItemFila:
    """Monta item da fila a partir dos dados salvos (sem rede, salvo fundamentos opcionais)."""
    pct_bazin: float | None = None
    pct_graham: float | None = None
    t = (ticker or "").strip().upper()
    datas = resumo_datas_com(proventos)
    if proventos.strip() and preco > 0:
        try:
            res = calculate(
                proventos,
                preco_atual=preco,
                dy_desejado=(dy or 6.0) / 100.0,
                ticker=t or "—",
                n_ultimos=n_anos,
                metodo=BaseMethod.MEDIA,
                criterio=YearCriterion.DATA_COM,
                incluir_ano_andamento=False,
            )
            if res.base and res.base > 0:
                baz = conta_bazin(res.base, preco, dy=BAZIN_DY)
                pct_bazin = baz.diferenca
        except Exception:  # noqa: BLE001
            pass
    lpa_u = lpa if lpa is not None else (fund.lpa if fund else None)
    vpa_u = vpa if vpa is not None else (fund.vpa if fund else None)
    if lpa_u is not None and vpa_u is not None and lpa_u > 0 and vpa_u > 0 and preco > 0:
        try:
            gra = conta_graham(lpa_u, vpa_u, preco)
            pct_graham = gra.diferenca
        except Exception:  # noqa: BLE001
            pass
    sinal = calcular_sinal(
        pct_bazin=pct_bazin,
        pct_graham=pct_graham,
        sentimento=sentimento,
        usar_sentimento=bool(sentimento),
        ticker=t or "—",
        preco=float(preco or 0),
        proventos=proventos,
        fund=fund,
        setor=setor or "outros",
        selic=selic,
        ipca_mais=ipca_mais,
        pl=fund.pl if fund else None,
        payout=fund.payout if fund else None,
    )
    return ItemFila(
        ticker=t or "—",
        preco=float(preco or 0),
        pct_bazin=pct_bazin,
        pct_graham=pct_graham,
        sinal=sinal,
        setor=setor or "outros",
        datas_com=datas,
    )
