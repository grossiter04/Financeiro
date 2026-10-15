"""Assistente de chat: contexto local + Gemini (indicativo, não recomendação)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from calculadora.avaliacao import (
    IPCA_MAIS_PADRAO,
    SELIC_PADRAO,
    montar_avaliacao,
)
from calculadora.carteira import PosicaoEnriquecida, enriquecer_posicoes
from calculadora.core import BAZIN_DY, BaseMethod, YearCriterion, calculate, conta_bazin, conta_graham
from calculadora.fundamentos import Fundamentos
from calculadora.gemini_client import chamar_gemini_texto, gemini_api_key
from calculadora.setores import normalizar_setor, rotulo_setor, setor_do_ticker
from calculadora.sinal import _pct_util
from calculadora.storage import AcaoSalva, PosicaoSalva, listar_acoes, listar_posicoes, obter_acao

_TICKER_RE = re.compile(r"\b([A-Z]{4}\d{1,2})\b", re.IGNORECASE)
# Raiz B3 sem classe (ex.: POMO) — resolvida contra candidatos locais
_RAIZ_RE = re.compile(r"\b([A-Z]{4})\b", re.IGNORECASE)
_STOP_RAIZ = frozenset(
    {
        "PARA",
        "COME",
        "COMO",
        "QUAL",
        "QUAIS",
        "ONDE",
        "SOBRE",
        "DEPO",
        "ANOS",
        "MESA",
        "MEUS",
        "ESSA",
        "ESSE",
        "ESTA",
        "ESTE",
        "TUDO",
        "NADA",
        "MAIS",
        "MENO",
        "BOAS",
        "VALE",  # "vale a pena…" ≠ VALE3 (só se candidato local)
        "PENA",  # "vale a pena"
        "COLO",  # colocar
        "CART",  # carteira truncada não; carteira tem 8 letras
        "PORQ",
        "PORQUE",
        "DIGA",
        "DIZE",
        "ELAS",
        "ELES",
        "UMA",
        "UMAS",
        "SEJA",
        "SERA",
        "SERÁ",
        "FAÇA",
        "FACA",
        "FEITO",
        "BEM",
        "MAL",
        "AQUI",
        "ALIA",
        "ALEM",
        "ALÉM",
        "ENTAO",
        "ENTÃO",
        "AGORA",
        "HOJE",
        "ONTEM",
    }
)

SYSTEM_PROMPT = """\
Você é o assistente da calculadora local de ações brasileiras (B3), focada em
dividendos, valuation e leitura de notícias. Responda em português do Brasil.

O CONTEXTO tem universos distintos:
- `carteira`: posições que o usuário JÁ tem (peso, resultado, decisão/score).
- `oportunidades`: ranking do catálogo para NOVOS aportes (pode incluir papéis
  fora da carteira). Campo `na_carteira` indica se já tem.
- `candidatos_reducao`: ranking das posições da CARTEIRA com leitura menos
  favorável (pior score / Evitar / mais esticadas) — use para perguntas de
  venda, redução ou “o que revisar”.
- `avaliacoes`: detalhe completo por ticker.

Mapa de decisão do modelo (indicativo):
- Comprar = leitura favorável a manter/aumentar (score alto).
- Esperar = neutro / sem convicção forte — NÃO é ordem de venda.
- Evitar = leitura desfavorável; na carteira, trate como candidato a REVISAR
  ou reduzir (nunca diga “venda agora” como ordem).

Regras de dados (obrigatórias):
1. Use APENAS o CONTEXTO JSON. Não invente preços, múltiplos, notícias, variações
   ou sinais. Se algo não estiver no JSON, diga que falta no contexto local.
2. Nunca diga "recomendo comprar/vender". Prefira: "pelo modelo local, a
   leitura é…".
3. Não garanta rentabilidade nem piso de cotação.
4. Ideias de aporte / baratas: use `oportunidades` (catálogo inteiro).
5. Análise da carteira / comprar vs vender / o que reduzir: responda as DUAS
   pontas, mesmo que uma esteja vazia:
   - Favoráveis a aportar/reforçar: `oportunidades` com na_carteira=true e
     melhor score, ou decisão Comprar na carteira.
   - Candidatos a revisar/reduzir: use `candidatos_reducao` (obrigatório
     citar 2–4 papéis com score/decisão e 1–2 motivos contra). Se a lista
     estiver vazia, diga que nenhuma posição está em Evitar e que as de
     menor score são as menos prioritárias para reforço — não invente venda.

Estrutura quando a pergunta for sobre carteira, aporte ou venda:
A) Favoráveis (aportar/reforçar) — tickers, score, 1–2 motivos a favor.
B) Revisar / reduzir — tickers de `candidatos_reducao`, score, motivos contra.
   Se perguntaram só de compra, ainda cite brevemente a ponta de redução.
C) Mercado e notícias (títulos do contexto).
D) Síntese + aviso de que é indicativo, não recomendação.

Tom: claro e útil, bullets curtos. Notícias e variação pesam tanto quanto
os múltiplos. Score ≈ −2 a +2 ( Compra tipicamente ≥ 1,0 ).
"""

SUGESTOES_PADRAO = (
    "Analise minha carteira: o que reforçar e o que revisar?",
    "Quais ações salvas parecem melhores para aportar?",
    "Compare as duas maiores posições da carteira",
)

_IDEIAS_RE = re.compile(
    r"aport|barat|comprar|oportun|ideias?|onde\s+coloc|boas?\s+a[cç]|\bmelhor(es)?\b|"
    r"qual(is)?\s+a[cç]|suger|indicação|indicacao|vale\s+a\s+pena\s+entrar|"
    r"refor[cç]|aument",
    re.IGNORECASE,
)
_REVISAO_RE = re.compile(
    r"vend[ae]|reduz|desfaz|sair|rebalancer|revis[aã]o|revisar|favor[aá]ve|"
    r"analis[ea].*carteira|carteira.*(compr|vend)|o\s+que\s+(vender|tirar|cortar)",
    re.IGNORECASE,
)


def pergunta_ideias_aporte(texto: str) -> bool:
    """True se a pergunta pede ideias de aporte / ranking, não só a carteira."""
    t = (texto or "").strip()
    if not t:
        return False
    if re.search(r"carteira", t, re.I) and not _IDEIAS_RE.search(t) and not _REVISAO_RE.search(t):
        return False
    return bool(_IDEIAS_RE.search(t) or _REVISAO_RE.search(t))


def pergunta_revisao_carteira(texto: str) -> bool:
    """True se pede análise comprar vs vender / o que reduzir na carteira."""
    return bool(_REVISAO_RE.search(texto or ""))


@dataclass
class MensagemChat:
    papel: str  # user | assistant
    texto: str


@dataclass
class ContextoChat:
    selic: float = SELIC_PADRAO
    ipca_mais: float = IPCA_MAIS_PADRAO
    foco_ticker: str | None = None
    modo: str = "geral"  # geral | ideias_aporte | revisao_carteira
    carteira: list[dict[str, Any]] = field(default_factory=list)
    avaliacoes: list[dict[str, Any]] = field(default_factory=list)
    oportunidades: list[dict[str, Any]] = field(default_factory=list)
    candidatos_reducao: list[dict[str, Any]] = field(default_factory=list)
    noticias: list[dict[str, Any]] = field(default_factory=list)
    noticias_setor: list[dict[str, Any]] = field(default_factory=list)
    mercado: list[dict[str, Any]] = field(default_factory=list)
    sentimento: list[dict[str, Any]] = field(default_factory=list)

    def para_prompt(self) -> str:
        return json.dumps(
            {
                "modo": self.modo,
                "selic": round(self.selic, 4),
                "ipca_mais": round(self.ipca_mais, 4),
                "foco_ticker": self.foco_ticker,
                "carteira": self.carteira,
                "oportunidades": self.oportunidades,
                "candidatos_reducao": self.candidatos_reducao,
                "avaliacoes": self.avaliacoes,
                "mercado": self.mercado,
                "sentimento_noticias": self.sentimento,
                "noticias_diretas": self.noticias,
                "noticias_setor": self.noticias_setor,
                "legenda": {
                    "carteira": "posições que o usuário já tem (+ decisão/score)",
                    "oportunidades": (
                        "ranking para NOVOS aportes / reforço (catálogo salvo)"
                    ),
                    "candidatos_reducao": (
                        "posições da CARTEIRA com pior leitura — candidatos a "
                        "revisar/reduzir (não é ordem de venda)"
                    ),
                    "avaliacoes": "detalhe de valuation por ticker",
                    "score": "nota composta ≈ −2 a +2; Comprar tipicamente ≥ 1,0",
                },
                "aviso": (
                    "Indicativo com base nos dados locais (fundamentos, "
                    "valuation, notícias RSS e variação de preço) — "
                    "não é recomendação de investimento. "
                    "Não invente fato fora deste JSON."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )


def extrair_tickers_texto(
    texto: str,
    *,
    candidatos: list[str] | None = None,
) -> list[str]:
    """
    Extrai tickers B3 do texto.

    Aceita ITUB4 e também raízes (POMO) resolvidas contra `candidatos`
    (abas abertas / salvas / carteira). Sem candidato local, assume
    hipóteses POMO3 e POMO4 para o app tentar carregar.
    """
    vistos: set[str] = set()
    out: list[str] = []
    for m in _TICKER_RE.finditer(texto or ""):
        t = m.group(1).upper()
        if t not in vistos:
            vistos.add(t)
            out.append(t)

    cands = [c.strip().upper().removesuffix(".SA") for c in (candidatos or []) if c]

    def _add(t: str) -> None:
        if t and t not in vistos:
            vistos.add(t)
            out.append(t)

    for m in _RAIZ_RE.finditer(texto or ""):
        raiz = m.group(1).upper()
        if raiz in _STOP_RAIZ:
            continue
        locais = [
            c
            for c in cands
            if c == raiz or (c.startswith(raiz) and c[len(raiz) :].isdigit())
        ]
        if locais:
            for t in locais:
                _add(t)
        elif cands:
            # há universo local, mas essa raiz não bate — não inventa ticker
            continue
        else:
            for suf in ("3", "4"):
                _add(f"{raiz}{suf}")
    return out


def _avaliacao_dict(
    *,
    ticker: str,
    preco: float,
    proventos: str,
    selic: float,
    ipca_mais: float,
    setor: str,
    fund: Fundamentos | None,
    pct_bazin: float | None,
    pct_graham: float | None,
    sentimento: str | None = None,
    sentimento_confiante: bool = False,
) -> dict[str, Any]:
    usar = bool(sentimento) and sentimento_confiante
    av = montar_avaliacao(
        ticker=ticker,
        preco=preco,
        proventos=proventos,
        pct_bazin=pct_bazin,
        pct_graham=pct_graham,
        selic=selic,
        ipca_mais=ipca_mais,
        fund=fund,
        setor=setor,
        pl=fund.pl if fund else None,
        payout=fund.payout if fund else None,
        sentimento=sentimento,
        usar_sentimento=usar,
    )
    return {
        "ticker": ticker,
        "setor": rotulo_setor(setor),
        "preco": round(preco, 4) if preco else None,
        "decisao": av.rotulo,
        "score": round(av.score, 4),
        "resumo": av.resumo,
        "dy_estimado": None if av.dy_estimado is None else round(av.dy_estimado, 4),
        "earnings_yield": None if av.earnings_yield is None else round(av.earnings_yield, 4),
        "fcf_yield": None if av.fcf_yield is None else round(av.fcf_yield, 4),
        "ev_ebit": None if av.ev_ebit is None else round(av.ev_ebit, 2),
        "pl": None if fund is None or fund.pl is None else round(float(fund.pl), 2),
        "roe": None if fund is None or fund.roe is None else round(float(fund.roe), 4),
        "p_vp": None if av.p_vp is None else round(av.p_vp, 2),
        "g_implicito": None if av.g_implicito is None else round(av.g_implicito, 4),
        "score_qualidade": None
        if av.score_qualidade is None
        else round(av.score_qualidade, 3),
        "score_valuation": None
        if av.score_valuation is None
        else round(av.score_valuation, 3),
        "sentimento_noticias": sentimento,
        "sentimento_confiante": sentimento_confiante,
        "vs_bazin": None if _pct_util(pct_bazin) is None else round(pct_bazin or 0, 4),
        "vs_graham": None if _pct_util(pct_graham) is None else round(pct_graham or 0, 4),
        "a_favor": av.a_favor[:6],
        "contra": av.contra[:6],
    }


def _metricas_bazin_graham(
    *,
    ticker: str,
    preco: float,
    dy: float,
    proventos: str,
    fund: Fundamentos | None,
) -> tuple[float | None, float | None]:
    pct_bazin: float | None = None
    pct_graham: float | None = None
    if proventos.strip() and preco > 0:
        try:
            res = calculate(
                proventos,
                preco_atual=preco,
                dy_desejado=(dy or 6.0) / 100.0 if dy > 1 else float(dy or 0.06),
                ticker=ticker,
                n_ultimos=5,
                metodo=BaseMethod.MEDIA,
                criterio=YearCriterion.DATA_COM,
                incluir_ano_andamento=False,
            )
            if res.base and res.base > 0:
                pct_bazin = conta_bazin(res.base, preco, dy=BAZIN_DY).diferenca
        except Exception:  # noqa: BLE001
            pass
    if (
        fund
        and fund.lpa is not None
        and fund.vpa is not None
        and fund.lpa > 0
        and fund.vpa > 0
        and preco > 0
    ):
        try:
            pct_graham = conta_graham(fund.lpa, fund.vpa, preco).diferenca
        except Exception:  # noqa: BLE001
            pass
    return pct_bazin, pct_graham


def montar_contexto_chat(
    *,
    pergunta: str = "",
    selic: float = SELIC_PADRAO,
    ipca_mais: float = IPCA_MAIS_PADRAO,
    foco_ticker: str | None = None,
    posicoes: list[PosicaoSalva] | None = None,
    acoes: list[AcaoSalva] | None = None,
    fund_cache: dict[str, Fundamentos | None] | None = None,
    precos: dict[str, float | None] | None = None,
    setores: dict[str, str] | None = None,
    noticias_por_ticker: dict[str, list[dict[str, str]]] | None = None,
    noticias_setor: list[dict[str, Any]] | None = None,
    sentimento_por_ticker: dict[str, dict[str, Any]] | None = None,
    mercado_por_ticker: dict[str, dict[str, Any]] | None = None,
    max_avaliacoes: int = 12,
    max_noticias_por_ticker: int = 5,
) -> ContextoChat:
    """
    Monta contexto reforçado para o prompt.

    Prioridade padrão: citados → foco → carteira → salvas.
    Em perguntas de ideias/aporte: citados → foco → salvas → carteira,
    com mais avaliações e bloco `oportunidades`.
    """
    posicoes = list(posicoes if posicoes is not None else listar_posicoes())
    acoes = list(acoes if acoes is not None else listar_acoes())
    fund_cache = fund_cache or {}
    precos = dict(precos or {})
    setores = dict(setores or {})
    noticias_por_ticker = noticias_por_ticker or {}
    sentimento_por_ticker = sentimento_por_ticker or {}
    mercado_por_ticker = mercado_por_ticker or {}

    ideias = pergunta_ideias_aporte(pergunta)
    revisao = pergunta_revisao_carteira(pergunta)
    if revisao:
        modo = "revisao_carteira"
    elif ideias:
        modo = "ideias_aporte"
    else:
        modo = "geral"
    if (ideias or revisao) and max_avaliacoes < 20:
        max_avaliacoes = 20

    por_ticker_acao = {a.ticker.upper(): a for a in acoes}
    tickers_carteira = {p.ticker.upper() for p in posicoes}
    for p in posicoes:
        t = p.ticker.upper()
        if t not in precos or precos[t] is None:
            salva = por_ticker_acao.get(t) or obter_acao(t)
            if salva and salva.preco > 0:
                precos[t] = float(salva.preco)
        if t not in setores:
            setores[t] = normalizar_setor(setor_do_ticker(t))

    enriq: list[PosicaoEnriquecida] = []
    if posicoes:
        enriq = enriquecer_posicoes(posicoes, precos, setores=setores)

    carteira_rows: list[dict[str, Any]] = []
    for e in sorted(enriq, key=lambda x: -(x.peso_pct or 0)):
        carteira_rows.append(
            {
                "ticker": e.ticker,
                "setor": rotulo_setor(e.setor),
                "quantidade": e.quantidade,
                "preco_medio": round(e.preco_medio, 4),
                "preco_atual": None if e.preco_atual is None else round(e.preco_atual, 4),
                "peso_pct": None if e.peso_pct is None else round(e.peso_pct, 4),
                "resultado_pct": None
                if e.resultado_pct is None
                else round(e.resultado_pct, 4),
                "valor_mercado": None
                if e.valor_mercado is None
                else round(e.valor_mercado, 2),
            }
        )

    candidatos = [a.ticker.upper() for a in acoes] + [p.ticker.upper() for p in posicoes]
    if foco_ticker:
        candidatos.append(str(foco_ticker).upper())
    citados = extrair_tickers_texto(pergunta, candidatos=candidatos)
    foco = (foco_ticker or "").strip().upper().removesuffix(".SA") or None
    ordem: list[str] = []
    base_prio = [e.ticker for e in enriq]
    salvas_prio = [a.ticker.upper() for a in acoes]
    # ideias: catálogo primeiro; senão carteira primeiro
    meio = salvas_prio + base_prio if ideias else base_prio + salvas_prio
    for t in citados + ([foco] if foco else []) + meio:
        if t and t not in ordem:
            ordem.append(t)

    avaliacoes: list[dict[str, Any]] = []
    sentimento_rows: list[dict[str, Any]] = []
    for t in ordem[:max_avaliacoes]:
        sent_info = sentimento_por_ticker.get(t) or {}
        sent = (sent_info.get("sentimento") or None) if sent_info else None
        conf = bool(sent_info.get("confiante")) if sent_info else False
        if sent:
            sentimento_rows.append(
                {
                    "ticker": t,
                    "sentimento": sent,
                    "confiante": conf,
                    "n_diretas": sent_info.get("n_diretas"),
                }
            )

        salva = por_ticker_acao.get(t) or obter_acao(t)
        preco = precos.get(t)
        if preco is None and salva and salva.preco > 0:
            preco = float(salva.preco)
        if preco is None or preco <= 0:
            if t in tickers_carteira:
                avaliacoes.append(
                    {
                        "ticker": t,
                        "na_carteira": True,
                        "aviso": "Sem preço atual para avaliar.",
                    }
                )
            continue
        proventos = salva.proventos if salva else ""
        dy_raw = float(salva.dy) if salva else 6.0
        dy_pct = dy_raw if dy_raw > 1 else dy_raw * 100.0
        fund = fund_cache.get(t)
        setor = setores.get(t) or normalizar_setor(setor_do_ticker(t))
        pct_b, pct_g = _metricas_bazin_graham(
            ticker=t,
            preco=float(preco),
            dy=dy_pct,
            proventos=proventos,
            fund=fund,
        )
        row = _avaliacao_dict(
            ticker=t,
            preco=float(preco),
            proventos=proventos,
            selic=selic,
            ipca_mais=ipca_mais,
            setor=setor,
            fund=fund,
            pct_bazin=pct_b,
            pct_graham=pct_g,
            sentimento=str(sent) if sent else None,
            sentimento_confiante=conf,
        )
        row["na_carteira"] = t in tickers_carteira
        avaliacoes.append(row)

    oportunidades = _montar_oportunidades(avaliacoes, limite=8)
    candidatos_reducao = _montar_candidatos_reducao(
        avaliacoes, carteira_rows, limite=6
    )
    # Enriquece carteira com decisão/score para o Gemini não “esquecer” a ponta venda
    por_av = {str(a.get("ticker", "")).upper(): a for a in avaliacoes if a.get("ticker")}
    for row in carteira_rows:
        av = por_av.get(str(row.get("ticker", "")).upper())
        if not av:
            continue
        row["decisao"] = av.get("decisao")
        row["score"] = av.get("score")
        row["resumo"] = av.get("resumo")
        row["contra"] = (av.get("contra") or [])[:2]

    noticias: list[dict[str, Any]] = []
    for t in ordem[:max_avaliacoes]:
        for n in (noticias_por_ticker.get(t) or [])[:max_noticias_por_ticker]:
            noticias.append({"ticker": t, **n})

    mercado_rows: list[dict[str, Any]] = []
    for t in ordem[:max_avaliacoes]:
        m = mercado_por_ticker.get(t)
        if not m:
            continue
        mercado_rows.append({"ticker": t, **m})

    return ContextoChat(
        selic=selic,
        ipca_mais=ipca_mais,
        foco_ticker=foco,
        modo=modo,
        carteira=carteira_rows,
        avaliacoes=avaliacoes,
        oportunidades=oportunidades,
        candidatos_reducao=candidatos_reducao,
        noticias=noticias,
        noticias_setor=list(noticias_setor or [])[:12],
        mercado=mercado_rows,
        sentimento=sentimento_rows,
    )


_ORDEM_DECISAO = {"Comprar": 0, "Esperar": 1, "Evitar": 2, "Sem dados": 3}
_ORDEM_REDUCAO = {"Evitar": 0, "Esperar": 1, "Comprar": 2, "Sem dados": 3}


def _montar_oportunidades(
    avaliacoes: list[dict[str, Any]], *, limite: int = 8
) -> list[dict[str, Any]]:
    """Ranking compacto do catálogo para perguntas de aporte."""
    rows: list[dict[str, Any]] = []
    for a in avaliacoes:
        if a.get("aviso") or not a.get("ticker"):
            continue
        decisao = str(a.get("decisao") or "Sem dados")
        rows.append(
            {
                "ticker": a["ticker"],
                "decisao": decisao,
                "score": a.get("score"),
                "setor": a.get("setor"),
                "na_carteira": bool(a.get("na_carteira")),
                "resumo": a.get("resumo"),
                "a_favor": (a.get("a_favor") or [])[:3],
                "contra": (a.get("contra") or [])[:2],
            }
        )
    rows.sort(
        key=lambda r: (
            _ORDEM_DECISAO.get(str(r.get("decisao")), 9),
            -(float(r["score"]) if r.get("score") is not None else -999),
        )
    )
    return rows[:limite]


def _montar_candidatos_reducao(
    avaliacoes: list[dict[str, Any]],
    carteira: list[dict[str, Any]],
    *,
    limite: int = 6,
) -> list[dict[str, Any]]:
    """
    Posições da carteira com pior leitura (Evitar primeiro, depois menor score).

    Não é ordem de venda — ranking para o assistente responder a ponta 'reduzir'.
    """
    na_cart = {str(c.get("ticker", "")).upper() for c in carteira if c.get("ticker")}
    peso = {
        str(c.get("ticker", "")).upper(): c.get("peso_pct")
        for c in carteira
        if c.get("ticker")
    }
    resultado = {
        str(c.get("ticker", "")).upper(): c.get("resultado_pct")
        for c in carteira
        if c.get("ticker")
    }
    rows: list[dict[str, Any]] = []
    for a in avaliacoes:
        t = str(a.get("ticker") or "").upper()
        if not t or t not in na_cart or a.get("aviso"):
            continue
        decisao = str(a.get("decisao") or "Sem dados")
        rows.append(
            {
                "ticker": t,
                "decisao": decisao,
                "score": a.get("score"),
                "peso_pct": peso.get(t),
                "resultado_pct": resultado.get(t),
                "setor": a.get("setor"),
                "resumo": a.get("resumo"),
                "contra": (a.get("contra") or [])[:3],
                "a_favor": (a.get("a_favor") or [])[:1],
                "leitura": (
                    "revisar/reduzir"
                    if decisao == "Evitar"
                    else (
                        "baixa prioridade para reforço"
                        if decisao == "Esperar"
                        else "leitura ok — não priorizar saída"
                    )
                ),
            }
        )
    rows.sort(
        key=lambda r: (
            _ORDEM_REDUCAO.get(str(r.get("decisao")), 9),
            float(r["score"]) if r.get("score") is not None else 999,
        )
    )
    return rows[:limite]


def _historico_para_prompt(historico: list[MensagemChat], *, limite: int = 8) -> str:
    linhas: list[str] = []
    for m in historico[-limite:]:
        quem = "Usuário" if m.papel == "user" else "Assistente"
        linhas.append(f"{quem}: {m.texto}")
    return "\n".join(linhas) if linhas else "(sem histórico)"


def montar_prompt_chat(
    pergunta: str,
    *,
    contexto: ContextoChat,
    historico: list[MensagemChat] | None = None,
) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"## CONTEXTO (dados locais — valuation + mercado + notícias)\n"
        f"{contexto.para_prompt()}\n\n"
        f"## HISTÓRICO RECENTE\n{_historico_para_prompt(historico or [])}\n\n"
        f"## PERGUNTA ATUAL\n{pergunta.strip()}\n"
    )


def responder_chat(
    pergunta: str,
    *,
    contexto: ContextoChat | None = None,
    historico: list[MensagemChat] | None = None,
    api_key: str | None = None,
) -> tuple[str, str]:
    """
    Responde com Gemini. Devolve (texto, modelo).

    Se não houver chave, levanta RuntimeError (a UI trata).
    """
    q = (pergunta or "").strip()
    if not q:
        raise ValueError("pergunta vazia")
    ctx = contexto or montar_contexto_chat(pergunta=q)
    prompt = montar_prompt_chat(q, contexto=ctx, historico=historico)
    key = (api_key if api_key is not None else gemini_api_key()).strip()
    return chamar_gemini_texto(prompt, api_key=key)
