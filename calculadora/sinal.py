"""Sinal orientativo: valuation (Bazin/Graham) + sentimento de notícias."""

from __future__ import annotations

from dataclasses import dataclass

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

# Código → rótulo curto (fila / badge) — tom neutro, sem ordem de trade
SINAIS = {
    "compra": "Atrativa",
    "aguardar": "Neutra",
    "cautela": "Esticada",
    "sem_dados": "Sem dados",
}

_CORES = {
    "compra": "#3f6f5a",
    "aguardar": "#7a6f5d",
    "cautela": "#8a5a5a",
    "sem_dados": "#6b7280",
}

# Percentuais absurdos (dados fracos / empresa sem DY) não entram no score
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


def _pontos_sentimento(sentimento: str | None) -> float | None:
    if not sentimento:
        return None
    s = sentimento.strip().lower()
    if s == "positivo":
        return 1.0
    if s == "negativo":
        return -1.0
    if s in ("neutro", "misto", "lateral"):
        return 0.0
    return None


def formatar_margem_pct(pct: float | None) -> str:
    """Texto curto para UI; evita números absurdos."""
    if pct is None:
        return "—"
    if abs(pct) > _PCT_MAX_UTIL:
        return "muito acima" if pct > 0 else "muito abaixo"
    return f"{pct * 100:+.0f}%"


def _rotulo_margem(pct: float | None, nome: str) -> str | None:
    if pct is None:
        return None
    if abs(pct) > _PCT_MAX_UTIL:
        extremo = "muito acima" if pct > 0 else "muito abaixo"
        return f"{nome} {extremo} (dado fraco)"
    lado = "abaixo" if pct < 0 else "acima"
    return f"{nome} {abs(pct) * 100:.0f}% {lado}"


# Valuation domina; notícias só entram com evidência direta e peso baixo.
_PESO_VALUATION = 0.85
_PESO_NOTICIAS = 0.15


def calcular_sinal(
    *,
    pct_bazin: float | None = None,
    pct_graham: float | None = None,
    sentimento: str | None = None,
    usar_sentimento: bool = False,
) -> SinalOrientativo:
    """
    Combina valuation e (opcionalmente) sentimento de notícias.

    - Valuation: média dos pontos Bazin/Graham disponíveis (peso 85%).
    - Notícias: só com ``usar_sentimento=True`` e citação direta (peso 15%).
    - Faixas mais conservadoras: atrativa (≥ +1.0), esticada (≤ −1.5),
      senão neutra. Códigos internos: compra / cautela / aguardar.
    """
    pts_val = [p for p in (_pontos_margem(pct_bazin), _pontos_margem(pct_graham)) if p is not None]
    pts_sent = _pontos_sentimento(sentimento) if usar_sentimento else None

    if not pts_val and pts_sent is None:
        return SinalOrientativo(
            codigo="sem_dados",
            rotulo=SINAIS["sem_dados"],
            score=0.0,
            resumo="Sem Bazin/Graham utilizável nem notícias diretas.",
        )

    partes: list[str] = []
    for pct, nome in ((pct_bazin, "Bazin"), (pct_graham, "Graham")):
        r = _rotulo_margem(pct, nome)
        if r:
            partes.append(r)

    if pts_val and pts_sent is not None:
        score = _PESO_VALUATION * (sum(pts_val) / len(pts_val)) + _PESO_NOTICIAS * pts_sent
        partes.append(f"notícias {sentimento}")
    elif pts_val:
        score = sum(pts_val) / len(pts_val)
    else:
        score = float(pts_sent or 0.0)
        if sentimento:
            partes.append(f"notícias {sentimento}")

    # Limiares menos agressivos: só extremos saem do neutro
    if score >= 1.0:
        codigo = "compra"
    elif score <= -1.5:
        codigo = "cautela"
    else:
        codigo = "aguardar"

    detalhe = (
        "Leitura suave vs Bazin/Graham"
        + (" e notícias diretas." if pts_sent is not None else ".")
        + " Indicativo — não é recomendação de investimento."
    )
    return SinalOrientativo(
        codigo=codigo,
        rotulo=SINAIS[codigo],
        score=round(score, 2),
        resumo="; ".join(partes) if partes else SINAIS[codigo],
        detalhe=detalhe,
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
) -> ItemFila:
    """Monta item da fila a partir dos dados salvos (sem rede, salvo LPA/VPA opcionais)."""
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
    if lpa is not None and vpa is not None and lpa > 0 and vpa > 0 and preco > 0:
        try:
            gra = conta_graham(lpa, vpa, preco)
            pct_graham = gra.diferenca
        except Exception:  # noqa: BLE001
            pass
    sinal = calcular_sinal(
        pct_bazin=pct_bazin,
        pct_graham=pct_graham,
        sentimento=sentimento,
        usar_sentimento=bool(sentimento),
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
