"""Relatório diário: notícias que afetam suas ações + números de cada uma."""

from __future__ import annotations

import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from calculadora.alertas import (
    _BRT,
    WatchItem,
    _smtp_config_from_env,
    _texto_proventos,
    enviar_email,
    load_watchlist,
)
from calculadora.core import (
    BAZIN_DY,
    BaseMethod,
    YearCriterion,
    calculate,
    conta_bazin,
    conta_graham,
)
from calculadora.fundamentos import fetch_fundamentos
from calculadora.noticias import (
    Empresa,
    Noticia,
    _sem_acento,
    buscar_noticias,
    ligar_noticias,
    load_empresas,
    raiz_ticker,
)
from calculadora.preco import fetch_historico

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELOS_PADRAO = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-flash-lite"]
MAX_NOTICIAS_IA = 60

_POSITIVAS = [
    "alta", "sobe", "subiu", "dispara", "avanca", "lucro", "recorde", "supera",
    "eleva", "aprova", "dividendo", "proventos", "jcp", "recompra", "upgrade", "otimis",
]
_NEGATIVAS = [
    "queda", "cai ", "caiu", "despenca", "recua", "prejuizo", "rebaix", "multa",
    "investigacao", "greve", "corta", "downgrade", "pessimis", "risco", "perde", "tomba",
]


@dataclass
class MetricaAcao:
    ticker: str
    nome: str
    setor: str
    preco: float | None = None
    var_dia: float | None = None
    var_mes: float | None = None
    teto_bazin: float | None = None
    pct_bazin: float | None = None
    teto_graham: float | None = None
    pct_graham: float | None = None
    teto_dy: float | None = None
    pct_dy: float | None = None
    dy_desejado: float = 6.0
    erros: list[str] = field(default_factory=list)


@dataclass
class AnaliseNoticia:
    id: str
    impacto: str = "neutro"
    relevancia: int = 1
    acoes: list[str] = field(default_factory=list)
    explicacao: str = ""


@dataclass
class AnaliseAcao:
    ticker: str
    tendencia: str = "lateral"
    sentimento: str = "neutro"
    expectativa: str = ""
    riscos: str = ""
    noticias: list[str] = field(default_factory=list)


@dataclass
class Relatorio:
    gerado_em: datetime
    metricas: list[MetricaAcao]
    noticias: list[Noticia]
    analise_noticias: dict[str, AnaliseNoticia]
    analise_acoes: dict[str, AnaliseAcao]
    resumo_mercado: str = ""
    destaques: list[str] = field(default_factory=list)
    motor: str = "regras"
    avisos: list[str] = field(default_factory=list)


def calcular_metricas(item: WatchItem, empresa: Empresa | None) -> MetricaAcao:
    m = MetricaAcao(
        ticker=item.ticker,
        nome=empresa.nome_principal if empresa else item.ticker,
        setor=empresa.setor if empresa else "outros",
        dy_desejado=item.dy_desejado,
    )
    try:
        hist = fetch_historico(item.ticker, periodo="1mo")
        fechamentos = [p.preco for p in hist.pontos if p.preco]
        if fechamentos:
            m.preco = fechamentos[-1]
            if len(fechamentos) >= 2 and fechamentos[-2]:
                m.var_dia = fechamentos[-1] / fechamentos[-2] - 1.0
            if fechamentos[0]:
                m.var_mes = fechamentos[-1] / fechamentos[0] - 1.0
    except Exception as exc:  # noqa: BLE001
        m.erros.append(f"preço: {exc}")
    if m.preco is None:
        return m

    try:
        texto, _ = _texto_proventos(item.ticker, timeout=20.0)
        res = calculate(
            texto,
            preco_atual=m.preco,
            dy_desejado=item.dy_desejado / 100.0,
            ticker=item.ticker,
            n_ultimos=item.n_anos,
            metodo=BaseMethod.MEDIA,
            criterio=YearCriterion.DATA_COM,
            incluir_ano_andamento=False,
        )
        m.teto_dy, m.pct_dy = res.preco_teto, res.diferenca
        if res.base and res.base > 0:
            baz = conta_bazin(res.base, m.preco, dy=BAZIN_DY)
            m.teto_bazin, m.pct_bazin = baz.preco_justo, baz.diferenca
    except Exception as exc:  # noqa: BLE001
        m.erros.append(f"proventos: {exc}")

    try:
        fund = fetch_fundamentos(item.ticker, timeout=15.0)
        if fund.lpa and fund.vpa and fund.lpa > 0 and fund.vpa > 0:
            gra = conta_graham(fund.lpa, fund.vpa, m.preco)
            m.teto_graham, m.pct_graham = gra.preco_justo, gra.diferenca
    except Exception as exc:  # noqa: BLE001
        m.erros.append(f"Graham: {exc}")
    return m


# ---------- análise sem IA ----------


def _impacto_por_palavras(n: Noticia) -> str:
    texto = _sem_acento(f"{n.titulo} {n.resumo}") + " "
    pos = sum(texto.count(p) for p in _POSITIVAS)
    neg = sum(texto.count(p) for p in _NEGATIVAS)
    if pos and neg:
        return "misto"
    if pos:
        return "positivo"
    if neg:
        return "negativo"
    return "neutro"


def _tendencia(m: MetricaAcao) -> str:
    ref = m.var_mes if m.var_mes is not None else m.var_dia
    if ref is None:
        return "sem dados"
    if ref > 0.02:
        return "alta"
    if ref < -0.02:
        return "queda"
    return "lateral"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.1f}%"


def analise_por_regras(
    metricas: list[MetricaAcao], noticias: list[Noticia]
) -> tuple[dict[str, AnaliseNoticia], dict[str, AnaliseAcao], str]:
    an_not: dict[str, AnaliseNoticia] = {}
    for n in noticias:
        an_not[n.id] = AnaliseNoticia(
            id=n.id,
            impacto=_impacto_por_palavras(n),
            relevancia=3 if n.acoes_diretas else 1,
            acoes=n.acoes,
            explicacao=(
                f"Cita diretamente: {', '.join(n.acoes_diretas)}."
                if n.acoes_diretas
                else f"Tema: {', '.join(n.temas)} (afeta o setor)."
            ),
        )

    an_acoes: dict[str, AnaliseAcao] = {}
    for m in metricas:
        ligadas = [n for n in noticias if m.ticker in n.acoes]
        diretas = [n for n in ligadas if m.ticker in n.acoes_diretas]
        pos = sum(1 for n in diretas if an_not[n.id].impacto == "positivo")
        neg = sum(1 for n in diretas if an_not[n.id].impacto == "negativo")
        sentimento = "positivo" if pos > neg else "negativo" if neg > pos else "neutro"

        partes = [f"Preço {_pct(m.var_mes)} no mês e {_pct(m.var_dia)} no último pregão."]
        if m.pct_bazin is not None:
            partes.append(
                f"Está {abs(m.pct_bazin) * 100:.1f}% {'abaixo' if m.pct_bazin < 0 else 'acima'} do teto Bazin."
            )
        if m.pct_graham is not None:
            partes.append(
                f"Está {abs(m.pct_graham) * 100:.1f}% {'abaixo' if m.pct_graham < 0 else 'acima'} do preço de Graham."
            )
        if diretas:
            partes.append(f"{len(diretas)} notícia(s) citando a empresa ({pos} positiva(s), {neg} negativa(s)).")
        elif ligadas:
            partes.append(f"Sem notícia direta; {len(ligadas)} notícia(s) de temas do setor.")

        an_acoes[m.ticker] = AnaliseAcao(
            ticker=m.ticker,
            tendencia=_tendencia(m),
            sentimento=sentimento,
            expectativa=" ".join(partes),
            noticias=[n.id for n in ligadas],
        )

    resumo = (
        "Análise automática simples (sem IA). Configure GEMINI_API_KEY para receber "
        "resumo do mercado e expectativas escritas para cada ação."
    )
    return an_not, an_acoes, resumo


# ---------- análise com Gemini ----------


def _prompt(metricas: list[MetricaAcao], noticias: list[Noticia], data_txt: str) -> str:
    acoes = [
        {
            "ticker": m.ticker,
            "empresa": m.nome,
            "setor": m.setor,
            "preco": m.preco,
            "var_ultimo_pregao_pct": None if m.var_dia is None else round(m.var_dia * 100, 2),
            "var_mes_pct": None if m.var_mes is None else round(m.var_mes * 100, 2),
            "margem_bazin_pct": None if m.pct_bazin is None else round(m.pct_bazin * 100, 1),
            "margem_graham_pct": None if m.pct_graham is None else round(m.pct_graham * 100, 1),
        }
        for m in metricas
    ]
    lista_noticias = [
        {
            "id": n.id,
            "fonte": n.fonte,
            "titulo": n.titulo,
            "resumo": n.resumo[:400],
            "citadas": n.acoes_diretas,
            "temas": n.temas,
        }
        for n in noticias
    ]
    return f"""Você é um analista de ações brasileiras. Hoje é {data_txt}.
O investidor acompanha as ações abaixo (foco em dividendos; margem negativa = preço abaixo do teto, ou seja, barata).

AÇÕES:
{json.dumps(acoes, ensure_ascii=False)}

NOTÍCIAS DAS ÚLTIMAS HORAS (sites brasileiros de investimento):
{json.dumps(lista_noticias, ensure_ascii=False)}

Tarefa, em português do Brasil:
1. "resumo_mercado": 4 a 7 frases sobre o cenário do dia (juros, dólar, Ibovespa, política, exterior) e o que isso significa para a carteira.
2. "destaques": 3 a 6 frases curtas com o que o investidor mais precisa saber hoje.
3. "noticias": só as notícias que realmente podem mexer com alguma das ações (ignore as irrelevantes). Para cada uma: id, impacto ("positivo", "negativo", "neutro" ou "misto"), relevancia (1 a 5), acoes (tickers da lista afetados) e explicacao (1 a 3 frases dizendo por quê e como afeta).
4. "acoes": TODAS as ações da lista. Para cada uma: ticker, tendencia ("alta", "queda" ou "lateral", com base na variação de preço), sentimento ("positivo", "negativo", "neutro" ou "misto", com base nas notícias), expectativa (2 a 4 frases combinando notícias, momento do preço e margens Bazin/Graham), riscos (1 a 2 frases) e noticias (ids relacionados).

Regras: use apenas as notícias fornecidas, não invente fatos nem números. Se não houver notícia sobre uma ação, diga isso e baseie-se no preço e no setor. Não dê ordem de compra ou venda; descreva o cenário.

Responda SOMENTE com JSON no formato:
{{"resumo_mercado": "", "destaques": [""], "noticias": [{{"id": "", "impacto": "", "relevancia": 1, "acoes": [""], "explicacao": ""}}], "acoes": [{{"ticker": "", "tendencia": "", "sentimento": "", "expectativa": "", "riscos": "", "noticias": [""]}}]}}"""


def _extrair_json(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim < 0:
        raise ValueError("resposta da IA sem JSON")
    return json.loads(texto[inicio : fim + 1])


def chamar_gemini(prompt: str, *, api_key: str, modelos: list[str], timeout: float = 240.0) -> tuple[dict, str]:
    erros: list[str] = []
    corpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    with httpx.Client(timeout=timeout) as client:
        for modelo in modelos:
            try:
                resp = client.post(
                    GEMINI_URL.format(model=modelo),
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=corpo,
                )
                if resp.status_code != 200:
                    erros.append(f"{modelo}: HTTP {resp.status_code} {resp.text[:200]}")
                    continue
                payload = resp.json()
                partes = payload["candidates"][0]["content"]["parts"]
                texto = "".join(p.get("text", "") for p in partes)
                return _extrair_json(texto), modelo
            except Exception as exc:  # noqa: BLE001
                erros.append(f"{modelo}: {exc}")
    raise RuntimeError("; ".join(erros) or "nenhum modelo disponível")


def _aplicar_ia(
    dados: dict,
    metricas: list[MetricaAcao],
    noticias: list[Noticia],
    an_not: dict[str, AnaliseNoticia],
    an_acoes: dict[str, AnaliseAcao],
) -> tuple[str, list[str]]:
    ids = {n.id for n in noticias}
    tickers = {m.ticker for m in metricas}

    ia_not: dict[str, AnaliseNoticia] = {}
    for raw in dados.get("noticias") or []:
        nid = str(raw.get("id", ""))
        if nid not in ids:
            continue
        try:
            relev = int(raw.get("relevancia", 1))
        except (TypeError, ValueError):
            relev = 1
        ia_not[nid] = AnaliseNoticia(
            id=nid,
            impacto=str(raw.get("impacto") or "neutro").lower(),
            relevancia=max(1, min(5, relev)),
            acoes=[t for t in (raw.get("acoes") or []) if t in tickers],
            explicacao=str(raw.get("explicacao") or ""),
        )
    if ia_not:
        an_not.clear()
        an_not.update(ia_not)

    for raw in dados.get("acoes") or []:
        t = str(raw.get("ticker", "")).upper()
        if t not in tickers:
            continue
        an_acoes[t] = AnaliseAcao(
            ticker=t,
            tendencia=str(raw.get("tendencia") or an_acoes.get(t, AnaliseAcao(t)).tendencia).lower(),
            sentimento=str(raw.get("sentimento") or "neutro").lower(),
            expectativa=str(raw.get("expectativa") or ""),
            riscos=str(raw.get("riscos") or ""),
            noticias=[i for i in (raw.get("noticias") or []) if i in ids],
        )

    destaques = [str(d) for d in (dados.get("destaques") or []) if str(d).strip()]
    return str(dados.get("resumo_mercado") or ""), destaques


# ---------- montagem ----------


def gerar_relatorio(
    *,
    watchlist_path: Path | None = None,
    empresas_path: Path | None = None,
    usar_ia: bool = True,
    horas: int = 36,
) -> Relatorio:
    agora = datetime.now(_BRT)
    itens = load_watchlist(watchlist_path)
    empresas = load_empresas(empresas_path)
    tickers = [i.ticker for i in itens]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futuro_noticias = pool.submit(buscar_noticias, horas=horas)
        metricas = list(pool.map(lambda i: calcular_metricas(i, empresas.get(raiz_ticker(i.ticker))), itens))
        todas, avisos = futuro_noticias.result()

    ligar_noticias(todas, tickers, empresas)
    relevantes = [n for n in todas if n.relevante]
    relevantes.sort(key=lambda n: (len(n.acoes_diretas) == 0, -len(n.acoes)))
    relevantes = relevantes[:MAX_NOTICIAS_IA]

    an_not, an_acoes, resumo = analise_por_regras(metricas, relevantes)
    destaques: list[str] = []
    motor = "regras"

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if usar_ia and api_key:
        env_modelo = os.environ.get("GEMINI_MODEL", "").strip()
        modelos = [env_modelo] + [m for m in GEMINI_MODELOS_PADRAO if m != env_modelo] if env_modelo else GEMINI_MODELOS_PADRAO
        try:
            dados, modelo = chamar_gemini(_prompt(metricas, relevantes, agora.strftime("%d/%m/%Y")), api_key=api_key, modelos=modelos)
            resumo_ia, destaques = _aplicar_ia(dados, metricas, relevantes, an_not, an_acoes)
            resumo = resumo_ia or resumo
            motor = modelo
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"IA indisponível, usei análise simples: {exc}")
            resumo = (
                "A IA do Google não respondeu desta vez (detalhes em \"Avisos técnicos\", no fim). "
                "Abaixo, a análise automática simples."
            )
    elif usar_ia:
        avisos.append("GEMINI_API_KEY não configurada: análise simples, sem IA.")

    for m in metricas:
        for e in m.erros:
            avisos.append(f"{m.ticker}: {e}")

    return Relatorio(
        gerado_em=agora,
        metricas=metricas,
        noticias=relevantes,
        analise_noticias=an_not,
        analise_acoes=an_acoes,
        resumo_mercado=resumo,
        destaques=destaques,
        motor=motor,
        avisos=avisos,
    )


# ---------- renderização ----------

_COR = {
    "positivo": "#15803d", "alta": "#15803d",
    "negativo": "#b91c1c", "queda": "#b91c1c",
    "misto": "#b45309", "neutro": "#475569", "lateral": "#475569", "sem dados": "#94a3b8",
}
_SETA = {"alta": "▲", "queda": "▼", "lateral": "▶", "sem dados": "·"}


def _e(texto: object) -> str:
    return html.escape(str(texto or ""))


def _badge(rotulo: str) -> str:
    cor = _COR.get(rotulo, "#475569")
    return (
        f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;'
        f'background:{cor};color:#fff;font-size:12px">{_e(rotulo)}</span>'
    )


def _cor_pct(v: float | None, *, barato_negativo: bool = False) -> str:
    if v is None:
        return "#94a3b8"
    bom = v < 0 if barato_negativo else v > 0
    return "#15803d" if bom else "#b91c1c" if v != 0 else "#475569"


def _td_pct(v: float | None, *, barato_negativo: bool = False) -> str:
    return f'<td style="padding:6px;text-align:right;color:{_cor_pct(v, barato_negativo=barato_negativo)}">{_pct(v)}</td>'


def _ordem_acoes(rel: Relatorio) -> list[MetricaAcao]:
    def chave(m: MetricaAcao):
        an = rel.analise_acoes.get(m.ticker)
        diretas = sum(1 for n in rel.noticias if m.ticker in n.acoes_diretas)
        return (-diretas, -(len(an.noticias) if an else 0), m.ticker)

    return sorted(rel.metricas, key=chave)


def _noticias_ordenadas(rel: Relatorio) -> list[Noticia]:
    def chave(n: Noticia):
        an = rel.analise_noticias.get(n.id)
        return (-(an.relevancia if an else 0), n.acoes_diretas == [])

    return sorted([n for n in rel.noticias if n.id in rel.analise_noticias], key=chave)


def renderizar_html(rel: Relatorio) -> str:
    data_txt = rel.gerado_em.strftime("%d/%m/%Y %H:%M")
    por_id = {n.id: n for n in rel.noticias}
    h: list[str] = [
        '<div style="font-family:Segoe UI,Arial,sans-serif;max-width:900px;color:#0f172a">',
        f'<h2 style="margin-bottom:4px">Relatório das suas ações — {data_txt}</h2>',
        f'<div style="color:#64748b;font-size:13px">{len(rel.metricas)} ações · '
        f'{len(rel.noticias)} notícias relevantes · análise: {_e(rel.motor)}</div>',
        '<h3>Cenário do dia</h3>',
        f'<p style="line-height:1.5">{_e(rel.resumo_mercado)}</p>',
    ]
    if rel.destaques:
        h.append("<ul>" + "".join(f"<li>{_e(d)}</li>" for d in rel.destaques) + "</ul>")

    h += [
        "<h3>Suas ações</h3>",
        '<table style="border-collapse:collapse;width:100%;font-size:13px">',
        '<tr style="background:#f1f5f9;text-align:left">'
        '<th style="padding:6px">Ação</th><th style="padding:6px;text-align:right">Preço</th>'
        '<th style="padding:6px;text-align:right">Dia</th><th style="padding:6px;text-align:right">Mês</th>'
        '<th style="padding:6px;text-align:right">vs Bazin</th><th style="padding:6px;text-align:right">vs Graham</th>'
        '<th style="padding:6px">Tendência</th><th style="padding:6px">Sentimento</th><th style="padding:6px">Notícias</th></tr>',
    ]
    for m in _ordem_acoes(rel):
        an = rel.analise_acoes.get(m.ticker) or AnaliseAcao(m.ticker)
        preco = "—" if m.preco is None else f"R$ {m.preco:.2f}"
        tend = an.tendencia
        h.append(
            '<tr style="border-bottom:1px solid #e2e8f0">'
            f'<td style="padding:6px"><b>{_e(m.ticker)}</b><br><span style="color:#64748b">{_e(m.nome)}</span></td>'
            f'<td style="padding:6px;text-align:right">{preco}</td>'
            + _td_pct(m.var_dia)
            + _td_pct(m.var_mes)
            + _td_pct(m.pct_bazin, barato_negativo=True)
            + _td_pct(m.pct_graham, barato_negativo=True)
            + f'<td style="padding:6px;color:{_COR.get(tend, "#475569")}">{_SETA.get(tend, "")} {_e(tend)}</td>'
            f'<td style="padding:6px">{_badge(an.sentimento)}</td>'
            f'<td style="padding:6px;text-align:center">{len(an.noticias)}</td></tr>'
        )
    h.append("</table>")
    h.append(
        '<p style="color:#64748b;font-size:12px">vs Bazin / vs Graham: negativo (verde) = '
        "preço abaixo do teto, ou seja, ação barata por esse critério.</p>"
    )

    h.append("<h3>O que esperar de cada ação</h3>")
    for m in _ordem_acoes(rel):
        an = rel.analise_acoes.get(m.ticker)
        if an is None:
            continue
        h.append(
            '<div style="border-left:4px solid '
            f'{_COR.get(an.sentimento, "#475569")};padding:6px 12px;margin:10px 0">'
            f"<b>{_e(m.ticker)}</b> — {_e(m.nome)} &nbsp;{_badge(an.tendencia)} {_badge(an.sentimento)}"
            f'<p style="margin:6px 0">{_e(an.expectativa)}</p>'
        )
        if an.riscos:
            h.append(f'<p style="margin:6px 0;color:#b45309"><b>Riscos:</b> {_e(an.riscos)}</p>')
        links = [por_id[i] for i in an.noticias if i in por_id][:5]
        if links:
            h.append(
                '<ul style="margin:4px 0;font-size:13px">'
                + "".join(f'<li><a href="{_e(n.link)}">{_e(n.titulo)}</a> <i>({_e(n.fonte)})</i></li>' for n in links)
                + "</ul>"
            )
        h.append("</div>")

    h.append("<h3>Notícias que podem mexer com a carteira</h3>")
    for n in _noticias_ordenadas(rel):
        an = rel.analise_noticias[n.id]
        hora = n.publicado.astimezone(_BRT).strftime("%d/%m %H:%M") if n.publicado else ""
        h.append(
            '<div style="padding:8px 0;border-bottom:1px solid #e2e8f0">'
            f'<a href="{_e(n.link)}" style="font-weight:600;color:#1d4ed8">{_e(n.titulo)}</a><br>'
            f'<span style="color:#64748b;font-size:12px">{_e(n.fonte)} · {hora} · relevância {an.relevancia}/5</span> '
            f"{_badge(an.impacto)}"
            f'<div style="font-size:13px;margin-top:4px"><b>Afeta:</b> {_e(", ".join(an.acoes) or "—")}</div>'
            f'<div style="font-size:13px">{_e(an.explicacao)}</div></div>'
        )

    if rel.avisos:
        h.append(
            '<details style="margin-top:16px;color:#64748b;font-size:12px"><summary>Avisos técnicos</summary><ul>'
            + "".join(f"<li>{_e(a)}</li>" for a in rel.avisos[:40])
            + "</ul></details>"
        )
    h.append(
        '<p style="color:#94a3b8;font-size:11px;margin-top:16px">Relatório automático a partir de notícias públicas. '
        "Não é recomendação de investimento.</p></div>"
    )
    return "\n".join(h)


def renderizar_texto(rel: Relatorio) -> str:
    linhas = [f"Relatório das suas ações — {rel.gerado_em.strftime('%d/%m/%Y %H:%M')}", ""]
    linhas += ["CENÁRIO DO DIA", rel.resumo_mercado, ""]
    for d in rel.destaques:
        linhas.append(f"- {d}")
    linhas += ["", "SUAS AÇÕES"]
    for m in _ordem_acoes(rel):
        an = rel.analise_acoes.get(m.ticker) or AnaliseAcao(m.ticker)
        preco = "—" if m.preco is None else f"R$ {m.preco:.2f}"
        linhas.append(
            f"{m.ticker} {preco} | dia {_pct(m.var_dia)} | mês {_pct(m.var_mes)} | "
            f"Bazin {_pct(m.pct_bazin)} | Graham {_pct(m.pct_graham)} | {an.tendencia} / {an.sentimento}"
        )
        if an.expectativa:
            linhas.append(f"  {an.expectativa}")
        if an.riscos:
            linhas.append(f"  Riscos: {an.riscos}")
    linhas += ["", "NOTÍCIAS"]
    for n in _noticias_ordenadas(rel):
        an = rel.analise_noticias[n.id]
        linhas.append(f"[{an.impacto}] {n.titulo} ({n.fonte})")
        linhas.append(f"  Afeta: {', '.join(an.acoes) or '—'} — {an.explicacao}")
        linhas.append(f"  {n.link}")
    linhas += ["", "Não é recomendação de investimento."]
    return "\n".join(linhas)


def enviar_relatorio(rel: Relatorio) -> None:
    smtp = _smtp_config_from_env()
    if not smtp:
        raise ValueError("Configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD e ALERT_EMAIL_TO.")
    enviar_email(
        renderizar_texto(rel),
        host=str(smtp["host"]),
        port=int(smtp["port"]),
        user=str(smtp["user"]),
        password=str(smtp["password"]),
        para=str(smtp["para"]),
        de=str(smtp["de"]),
        assunto=f"Relatório do dia {rel.gerado_em.strftime('%d/%m')}: notícias das suas ações",
        html=renderizar_html(rel),
    )
