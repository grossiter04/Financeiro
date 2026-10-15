"""Avaliação unificada barata/cara: 5 pilares → Comprar / Esperar / Evitar."""

from __future__ import annotations

from dataclasses import dataclass, field

from calculadora.carteira import dy_estimado, estimar_proventos_acao
from calculadora.core import resumo_datas_com
from calculadora.dcf import crescimento_implicito
from calculadora.fundamentos import (
    Fundamentos,
    _earnings_yield,
    _ev_ebit,
    _fcf_yield,
    _p_vp,
)

SELIC_PADRAO = 0.1475
IPCA_MAIS_PADRAO = 0.06  # yield real NTN-B aproximado; ajustável na UI

_PCT_MAX_UTIL = 1.50  # ±150%


def _pct_util(pct: float | None) -> float | None:
    if pct is None:
        return None
    if abs(pct) > _PCT_MAX_UTIL:
        return None
    return pct


def _pontos_margem(pct: float | None) -> float | None:
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
    if pct is None:
        return "—"
    if abs(pct) > _PCT_MAX_UTIL:
        return "muito acima" if pct > 0 else "muito abaixo"
    return f"{pct * 100:+.0f}%"

DECISOES = {
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

# Pesos dos pilares (renormalizados pelos disponíveis)
_PESO_VALUATION = 0.40
_PESO_OPORTUNIDADE = 0.25
_PESO_QUALIDADE = 0.20
_PESO_EXPECTATIVAS = 0.10
_PESO_NOTICIAS = 0.15

_QUALIDADE_FRACA = -0.5  # veto de compra se valuation barato
_CICLICOS = frozenset({"petroleo", "mineracao", "papel", "agro"})
_FINANCEIROS = frozenset({"bancos", "seguros"})


@dataclass(frozen=True)
class Motivo:
    sentido: str  # a_favor | contra | neutro
    texto: str
    pilar: str = ""  # qualidade | valuation | oportunidade | expectativas | noticias | geral


@dataclass
class Avaliacao:
    decisao: str  # compra | aguardar | cautela | sem_dados
    rotulo: str
    score: float
    resumo: str
    motivos: list[Motivo] = field(default_factory=list)
    dy_estimado: float | None = None
    earnings_yield: float | None = None
    fcf_yield: float | None = None
    ev_ebit: float | None = None
    p_vp: float | None = None
    g_implicito: float | None = None
    score_qualidade: float | None = None
    score_valuation: float | None = None
    sentimento: str | None = None
    detalhe: str = (
        "Indicativo — não é recomendação de investimento. "
        "Combina qualidade, múltiplos, juro real e notícias diretas."
    )

    @property
    def cor(self) -> str:
        return _CORES.get(self.decisao, _CORES["sem_dados"])

    @property
    def a_favor(self) -> list[str]:
        return [m.texto for m in self.motivos if m.sentido == "a_favor"]

    @property
    def contra(self) -> list[str]:
        return [m.texto for m in self.motivos if m.sentido == "contra"]


def _fmt_pct_pt(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%".replace(".", ",")


def _fmt_x(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.1f}x".replace(".", ",")


def _setor_chave(setor: str) -> str:
    return (setor or "").strip().lower()


def _pontos_qualidade(
    *,
    roe: float | None,
    payout: float | None,
    fcf: float | None,
    divida: float | None,
    ev: float | None,
    selic: float,
    ke: float | None = None,
) -> tuple[float | None, list[Motivo]]:
    motivos: list[Motivo] = []
    pontos: list[float] = []
    custo = ke if ke is not None else selic + 0.05

    if roe is not None:
        if roe >= custo + 0.03:
            pontos.append(1.5)
            motivos.append(
                Motivo(
                    "a_favor",
                    f"ROE {_fmt_pct_pt(roe)} acima do custo de capital ≈ {_fmt_pct_pt(custo)}",
                    "qualidade",
                )
            )
        elif roe >= custo:
            pontos.append(0.5)
            motivos.append(
                Motivo("a_favor", f"ROE {_fmt_pct_pt(roe)} próximo do custo de capital", "qualidade")
            )
        elif roe > 0:
            pontos.append(-1.0)
            motivos.append(
                Motivo(
                    "contra",
                    f"ROE {_fmt_pct_pt(roe)} abaixo do custo ≈ {_fmt_pct_pt(custo)} (destrói valor)",
                    "qualidade",
                )
            )
        else:
            pontos.append(-1.5)
            motivos.append(Motivo("contra", f"ROE {_fmt_pct_pt(roe)} negativo", "qualidade"))

    if payout is not None and payout > 0:
        if payout > 1.2:
            pontos.append(-1.0)
            motivos.append(
                Motivo(
                    "contra",
                    f"Payout {_fmt_pct_pt(payout)} acima do lucro — pode não se repetir",
                    "qualidade",
                )
            )
        elif 0.3 <= payout <= 0.8:
            pontos.append(0.5)
            motivos.append(Motivo("a_favor", f"Payout {_fmt_pct_pt(payout)} razoável", "qualidade"))

    if fcf is not None:
        if fcf < 0:
            pontos.append(-1.2)
            motivos.append(Motivo("contra", "FCF negativo (caixa operacional fraco)", "qualidade"))
        elif fcf > 0:
            pontos.append(0.6)
            motivos.append(Motivo("a_favor", "FCF positivo", "qualidade"))

    if divida is not None and ev is not None and ev > 0:
        alav = divida / ev
        if alav >= 0.55:
            pontos.append(-1.0)
            motivos.append(
                Motivo("contra", f"Dívida/EV {_fmt_pct_pt(alav)} elevada", "qualidade")
            )
        elif alav <= 0.25:
            pontos.append(0.4)
            motivos.append(Motivo("a_favor", f"Dívida/EV {_fmt_pct_pt(alav)} controlada", "qualidade"))

    if not pontos:
        return None, motivos
    return sum(pontos) / len(pontos), motivos


def _pontos_valuation(
    *,
    setor: str,
    pct_bazin: float | None,
    pct_graham: float | None,
    pl: float | None,
    p_vp: float | None,
    ev_ebit: float | None,
    fcf_yield: float | None,
    roe: float | None,
) -> tuple[float | None, list[Motivo]]:
    motivos: list[Motivo] = []
    pontos: list[float] = []
    pesos: list[float] = []
    chave = _setor_chave(setor)
    ciclico = chave in _CICLICOS
    financeiro = chave in _FINANCEIROS

    # Bazin — sempre relevante para tese de proventos
    bz = _pct_util(pct_bazin)
    pb = _pontos_margem(pct_bazin)
    if pb is not None:
        w = 1.0 if not financeiro else 0.7
        pontos.append(pb * w)
        pesos.append(w)
        if bz is not None:
            if bz <= -0.15:
                motivos.append(
                    Motivo("a_favor", f"Bazin: {formatar_margem_pct(bz)} vs teto de proventos", "valuation")
                )
            elif bz <= -0.05:
                motivos.append(Motivo("a_favor", f"Bazin: {formatar_margem_pct(bz)}", "valuation"))
            elif bz < 0.08:
                motivos.append(
                    Motivo("neutro", f"Bazin: {formatar_margem_pct(bz)} (perto do teto)", "valuation")
                )
            elif bz < 0.25:
                motivos.append(
                    Motivo("contra", f"Bazin: {formatar_margem_pct(bz)} (esticada)", "valuation")
                )
            else:
                motivos.append(Motivo("contra", f"Bazin: {formatar_margem_pct(bz)} (cara)", "valuation"))
    elif pct_bazin is not None:
        motivos.append(Motivo("neutro", "Bazin: margem extrema (dado fraco)", "valuation"))

    # Graham / P/VP — peso alto em bancos
    gr = _pct_util(pct_graham)
    pg = _pontos_margem(pct_graham)
    if pg is not None:
        w = 1.2 if financeiro else 0.5
        pontos.append(pg * w)
        pesos.append(w)
        if gr is not None:
            if gr <= -0.15:
                motivos.append(Motivo("a_favor", f"Graham: {formatar_margem_pct(gr)}", "valuation"))
            elif gr >= 0.25:
                motivos.append(
                    Motivo("contra", f"Graham: {formatar_margem_pct(gr)} (cara no modelo)", "valuation")
                )

    if financeiro and p_vp is not None and roe is not None:
        # P/VP justo aproximado: ROE alto justifica P/VP > 1
        if roe >= 0.15 and p_vp <= 1.5:
            pontos.append(1.0)
            pesos.append(0.8)
            motivos.append(
                Motivo(
                    "a_favor",
                    f"P/VP {_fmt_x(p_vp)} com ROE {_fmt_pct_pt(roe)} (banco/seguradora)",
                    "valuation",
                )
            )
        elif roe < 0.10 and p_vp >= 1.2:
            pontos.append(-1.0)
            pesos.append(0.8)
            motivos.append(
                Motivo(
                    "contra",
                    f"P/VP {_fmt_x(p_vp)} alto para ROE {_fmt_pct_pt(roe)}",
                    "valuation",
                )
            )

    # EV/EBIT e FCF yield — gerais (não financeiros)
    if not financeiro:
        if ev_ebit is not None and 0 < ev_ebit < 80:
            if ev_ebit <= 8:
                pontos.append(1.5)
                pesos.append(1.0)
                motivos.append(Motivo("a_favor", f"EV/EBIT {_fmt_x(ev_ebit)} atrativo", "valuation"))
            elif ev_ebit <= 12:
                pontos.append(0.5)
                pesos.append(1.0)
                motivos.append(Motivo("a_favor", f"EV/EBIT {_fmt_x(ev_ebit)} razoável", "valuation"))
            elif ev_ebit >= 22:
                pontos.append(-1.2)
                pesos.append(1.0)
                motivos.append(Motivo("contra", f"EV/EBIT {_fmt_x(ev_ebit)} elevado", "valuation"))
            else:
                pesos.append(0.3)
                pontos.append(0.0)
                motivos.append(Motivo("neutro", f"EV/EBIT {_fmt_x(ev_ebit)}", "valuation"))

        if fcf_yield is not None:
            if fcf_yield >= 0.08:
                pontos.append(1.5)
                pesos.append(1.0)
                motivos.append(
                    Motivo("a_favor", f"FCF yield {_fmt_pct_pt(fcf_yield)}", "valuation")
                )
            elif fcf_yield >= 0.05:
                pontos.append(0.6)
                pesos.append(0.8)
                motivos.append(
                    Motivo("a_favor", f"FCF yield {_fmt_pct_pt(fcf_yield)}", "valuation")
                )
            elif fcf_yield < 0:
                pontos.append(-1.0)
                pesos.append(1.0)
                motivos.append(Motivo("contra", "FCF yield negativo", "valuation"))
            elif fcf_yield < 0.03:
                pontos.append(-0.6)
                pesos.append(0.6)
                motivos.append(
                    Motivo("contra", f"FCF yield {_fmt_pct_pt(fcf_yield)} baixo", "valuation")
                )

    # P/L — peso reduzido em cíclicas
    if pl is not None and 0 < pl < 80:
        w = 0.3 if ciclico else 0.6
        if pl <= 10:
            pontos.append(0.8 * w)
            pesos.append(w)
            motivos.append(
                Motivo(
                    "a_favor" if not ciclico else "neutro",
                    f"P/L {pl:.1f}x".replace(".", ",")
                    + (" (cuidado: cíclica)" if ciclico else ""),
                    "valuation",
                )
            )
        elif pl >= 22:
            pontos.append(-0.8 * w)
            pesos.append(w)
            motivos.append(Motivo("contra", f"P/L {pl:.1f}x elevado".replace(".", ","), "valuation"))

    if ciclico:
        motivos.append(
            Motivo(
                "neutro",
                "Setor cíclico: lucro/P/L de pico pode enganar — prefira FCF e média de ciclo",
                "valuation",
            )
        )

    if not pontos:
        return None, motivos
    # média ponderada simples
    total_w = sum(pesos) if pesos else len(pontos)
    score = sum(pontos) / total_w if total_w else 0.0
    # normalizar para faixa ~[-2, 2] como _pontos_margem
    score = max(-2.0, min(2.0, score))
    return score, motivos


def _pontos_oportunidade(
    *,
    dy: float | None,
    ey: float | None,
    selic: float,
    ipca_mais: float,
    n_anos_proventos: int,
) -> tuple[float | None, list[Motivo]]:
    motivos: list[Motivo] = []
    pontos: list[float] = []

    # Melhor entre DY e earnings yield vs Selic / IPCA+
    rendimento = None
    rotulo = ""
    if dy is not None and ey is not None:
        if dy >= ey:
            rendimento, rotulo = dy, "DY estimado"
        else:
            rendimento, rotulo = ey, "earnings yield"
    elif dy is not None:
        rendimento, rotulo = dy, "DY estimado"
    elif ey is not None:
        rendimento, rotulo = ey, "earnings yield"

    if rendimento is not None:
        # vs IPCA+ (juro real) — régua principal BR
        if rendimento >= ipca_mais + 0.04:
            pontos.append(2.0)
            motivos.append(
                Motivo(
                    "a_favor",
                    f"{rotulo} {_fmt_pct_pt(rendimento)} bem acima do IPCA+ {_fmt_pct_pt(ipca_mais)}",
                    "oportunidade",
                )
            )
        elif rendimento >= ipca_mais:
            pontos.append(1.0)
            motivos.append(
                Motivo(
                    "a_favor",
                    f"{rotulo} {_fmt_pct_pt(rendimento)} ≥ IPCA+ {_fmt_pct_pt(ipca_mais)}",
                    "oportunidade",
                )
            )
        elif rendimento >= ipca_mais - 0.02:
            pontos.append(0.0)
            motivos.append(
                Motivo(
                    "neutro",
                    f"{rotulo} {_fmt_pct_pt(rendimento)} perto do IPCA+ {_fmt_pct_pt(ipca_mais)}",
                    "oportunidade",
                )
            )
        else:
            pontos.append(-1.5)
            motivos.append(
                Motivo(
                    "contra",
                    f"{rotulo} {_fmt_pct_pt(rendimento)} abaixo do IPCA+ {_fmt_pct_pt(ipca_mais)}",
                    "oportunidade",
                )
            )

        # vs Selic (custo de oportunidade nominal)
        if rendimento >= selic:
            pontos.append(1.0)
            motivos.append(
                Motivo(
                    "a_favor",
                    f"{rotulo} {_fmt_pct_pt(rendimento)} ≥ Selic {_fmt_pct_pt(selic)}",
                    "oportunidade",
                )
            )
        elif rendimento >= selic - 0.03:
            pontos.append(0.0)
            motivos.append(
                Motivo(
                    "neutro",
                    f"{rotulo} {_fmt_pct_pt(rendimento)} um pouco abaixo da Selic",
                    "oportunidade",
                )
            )
        else:
            pontos.append(-1.0)
            motivos.append(
                Motivo(
                    "contra",
                    f"{rotulo} {_fmt_pct_pt(rendimento)} bem abaixo da Selic {_fmt_pct_pt(selic)}",
                    "oportunidade",
                )
            )
    else:
        # Sem DY/EY: não inventa penalidade — pilar fica ausente se também não houver anos
        pass

    if n_anos_proventos >= 3:
        pontos.append(0.8)
        motivos.append(
            Motivo(
                "a_favor",
                f"Proventos consistentes em {n_anos_proventos} anos fechados",
                "oportunidade",
            )
        )
    elif n_anos_proventos == 2:
        pontos.append(0.2)
        motivos.append(Motivo("neutro", "Só 2 anos fechados de proventos", "oportunidade"))
    elif n_anos_proventos == 1:
        pontos.append(-0.4)
        motivos.append(Motivo("contra", "Histórico de proventos curto (1 ano)", "oportunidade"))

    if not pontos:
        return None, motivos
    return sum(pontos) / len(pontos), motivos


def _pontos_expectativas(
    *,
    g_implicito: float | None,
    g_historico: float | None,
) -> tuple[float | None, list[Motivo]]:
    motivos: list[Motivo] = []
    if g_implicito is None:
        return None, motivos

    ref = g_historico if g_historico is not None else 0.05
    if g_implicito <= ref + 0.02:
        score = 1.0
        motivos.append(
            Motivo(
                "a_favor",
                f"Preço embute g ≈ {_fmt_pct_pt(g_implicito)} "
                f"(plausível vs histórico {_fmt_pct_pt(g_historico)})",
                "expectativas",
            )
        )
    elif g_implicito <= ref + 0.08:
        score = 0.0
        motivos.append(
            Motivo(
                "neutro",
                f"Preço embute g ≈ {_fmt_pct_pt(g_implicito)} — exige crescimento sólido",
                "expectativas",
            )
        )
    else:
        score = -1.2
        motivos.append(
            Motivo(
                "contra",
                f"Preço embute g ≈ {_fmt_pct_pt(g_implicito)} alto "
                f"(histórico {_fmt_pct_pt(g_historico)}) — expectativas agressivas",
                "expectativas",
            )
        )
    return score, motivos


def _pontos_noticias(
    sentimento: str | None,
    *,
    valuation_barata: bool,
) -> tuple[float | None, list[Motivo]]:
    motivos: list[Motivo] = []
    if not sentimento:
        return None, motivos
    s = sentimento.strip().lower()
    if s == "positivo":
        motivos.append(Motivo("a_favor", "Notícias diretas com tom positivo", "noticias"))
        return 1.0, motivos
    if s == "negativo":
        txt = "Notícias diretas negativas"
        if valuation_barata:
            txt += " — possível armadilha de valor (barata por um motivo)"
        motivos.append(Motivo("contra", txt, "noticias"))
        return -1.0, motivos
    if s in ("neutro", "misto", "lateral"):
        motivos.append(Motivo("neutro", f"Notícias diretas: {s}", "noticias"))
        return 0.0, motivos
    return None, motivos


def montar_avaliacao(
    *,
    ticker: str,
    preco: float,
    proventos: str = "",
    pct_bazin: float | None = None,
    pct_graham: float | None = None,
    selic: float = SELIC_PADRAO,
    ipca_mais: float = IPCA_MAIS_PADRAO,
    pl: float | None = None,
    payout: float | None = None,
    fund: Fundamentos | None = None,
    setor: str = "",
    peso_setor_carteira: float | None = None,
    n_anos_dy: int = 3,
    sentimento: str | None = None,
    usar_sentimento: bool = False,
    g_implicito: float | None = None,
    g_historico: float | None = None,
    fcf0: float | None = None,
    wacc: float | None = None,
    divida_liquida: float | None = None,
    acoes: float | None = None,
) -> Avaliacao:
    """
    Combina qualidade, múltiplos por setor, juro BR, reverse DCF e notícias.

    Pilares sem dado são ignorados (pesos renormalizados). Qualidade fraca +
    valuation barato veta \"Comprar\" (armadilha de valor).
    """
    t = (ticker or "").strip().upper() or "—"
    motivos: list[Motivo] = []

    if fund is not None:
        pl = pl if pl is not None else fund.pl
        payout = payout if payout is not None else fund.payout

    ey = _earnings_yield(pl)
    ev = fund.ev if fund else None
    ebit = fund.ebit if fund else None
    ebitda = fund.ebitda if fund else None
    fcf = fund.fcf if fund else (fcf0 if fcf0 is not None else None)
    roe = fund.roe if fund else None
    divida = fund.divida if fund else None
    caixa = fund.caixa if fund else None
    beta = fund.beta if fund else None
    acoes_f = acoes if acoes is not None else (fund.acoes if fund else None)
    vpa = fund.vpa if fund else None

    ev_ebit = _ev_ebit(ev, ebit, ebitda)
    fcf_y = _fcf_yield(fcf, ev)
    pvp = _p_vp(preco if preco > 0 else None, vpa)

    est = estimar_proventos_acao(
        ticker=t, quantidade=1.0, proventos_texto=proventos or "", n_anos=n_anos_dy
    )
    dy = dy_estimado(est.por_acao_medio, preco if preco > 0 else None)
    datas = resumo_datas_com(proventos or "")
    n_anos = len(est.anos_usados)

    ke = (selic + (beta if beta is not None else 1.0) * 0.05)

    sq, mq = _pontos_qualidade(
        roe=roe,
        payout=payout,
        fcf=fcf,
        divida=divida,
        ev=ev,
        selic=selic,
        ke=ke,
    )
    motivos.extend(mq)

    sv, mv = _pontos_valuation(
        setor=setor,
        pct_bazin=pct_bazin,
        pct_graham=pct_graham,
        pl=pl,
        p_vp=pvp,
        ev_ebit=ev_ebit,
        fcf_yield=fcf_y,
        roe=roe,
    )
    motivos.extend(mv)

    so, mo = _pontos_oportunidade(
        dy=dy,
        ey=ey,
        selic=selic,
        ipca_mais=ipca_mais,
        n_anos_proventos=n_anos,
    )
    motivos.extend(mo)

    # Reverse DCF on-the-fly se dados bastarem e g não veio pronto
    g_imp = g_implicito
    g_hist = g_historico
    if g_imp is None and fcf is not None and fcf > 0 and acoes_f and acoes_f > 0 and preco > 0:
        dl = 0.0
        if divida is not None or caixa is not None:
            dl = float(divida or 0.0) - float(caixa or 0.0)
        w = wacc if wacc is not None else ke
        rev = crescimento_implicito(
            preco_atual=preco,
            fcf0=float(fcf),
            wacc=float(w),
            divida_liquida=dl if divida_liquida is None else float(divida_liquida),
            acoes=float(acoes_f),
            g_historico=g_hist,
        )
        if rev.disponivel:
            g_imp = rev.g_implicito
            for a in rev.avisos:
                motivos.append(Motivo("neutro", a, "expectativas"))

    se, me = _pontos_expectativas(g_implicito=g_imp, g_historico=g_hist)
    motivos.extend(me)

    valuation_barata = sv is not None and sv >= 0.8
    sn: float | None = None
    if usar_sentimento:
        sn, mn = _pontos_noticias(sentimento, valuation_barata=valuation_barata)
        motivos.extend(mn)

    if peso_setor_carteira is not None and peso_setor_carteira >= 0.30:
        motivos.append(
            Motivo(
                "contra",
                f"{setor or 'Este setor'} já pesa {_fmt_pct_pt(peso_setor_carteira)} na sua carteira",
                "geral",
            )
        )
        # leve penalidade no score via qualidade/oportunidade artificial
        if so is not None:
            so = so - 0.4
        elif sq is not None:
            sq = sq - 0.4

    if datas.proxima:
        motivos.append(
            Motivo("a_favor", f"Próxima data-com: {datas.fmt_proxima()}", "geral")
        )

    pilares: list[tuple[float, float]] = []  # (peso, score)
    if sv is not None:
        pilares.append((_PESO_VALUATION, sv))
    if so is not None:
        pilares.append((_PESO_OPORTUNIDADE, so))
    if sq is not None:
        pilares.append((_PESO_QUALIDADE, sq))
    if se is not None:
        pilares.append((_PESO_EXPECTATIVAS, se))
    if sn is not None:
        pilares.append((_PESO_NOTICIAS, sn))

    if not pilares:
        return Avaliacao(
            decisao="sem_dados",
            rotulo=DECISOES["sem_dados"],
            score=0.0,
            resumo="Faltam dados para montar a avaliação.",
            motivos=motivos or [Motivo("neutro", "Sem dados utilizáveis")],
            dy_estimado=dy,
            earnings_yield=ey,
            fcf_yield=fcf_y,
            ev_ebit=ev_ebit,
            p_vp=pvp,
            g_implicito=g_imp,
            score_qualidade=sq,
            score_valuation=sv,
            sentimento=sentimento,
        )

    peso_total = sum(p for p, _ in pilares)
    score = sum(p * s for p, s in pilares) / peso_total if peso_total else 0.0

    qualidade_fraca = sq is not None and sq <= _QUALIDADE_FRACA
    bazin_esticado = (_pct_util(pct_bazin) or -1) >= 0.20
    dy_fraco = dy is not None and dy < selic - 0.02

    if score >= 1.0 and not (qualidade_fraca and valuation_barata) and not bazin_esticado:
        decisao = "compra"
    elif qualidade_fraca and valuation_barata and score >= 0.5:
        # Armadilha: parece barata mas qualidade ruim → no máximo Esperar
        decisao = "aguardar"
        motivos.append(
            Motivo(
                "contra",
                "Parece barata, mas a qualidade é fraca — risco de armadilha de valor",
                "qualidade",
            )
        )
    elif score <= -1.5 or (bazin_esticado and dy_fraco):
        decisao = "cautela"
    else:
        decisao = "aguardar"

    favor = [m.texto for m in motivos if m.sentido == "a_favor"]
    contra = [m.texto for m in motivos if m.sentido == "contra"]
    if favor:
        resumo = favor[0]
    elif contra:
        resumo = contra[0]
    else:
        resumo = DECISOES[decisao]

    return Avaliacao(
        decisao=decisao,
        rotulo=DECISOES[decisao],
        score=round(score, 2),
        resumo=resumo,
        motivos=motivos,
        dy_estimado=dy,
        earnings_yield=ey,
        fcf_yield=fcf_y,
        ev_ebit=ev_ebit,
        p_vp=pvp,
        g_implicito=g_imp,
        score_qualidade=sq,
        score_valuation=sv,
        sentimento=sentimento,
    )
