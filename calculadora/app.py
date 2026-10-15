"""Calculadora de preço teto por proventos — interface Streamlit."""

from __future__ import annotations

import html
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Streamlit roda este arquivo como script: a raiz do projeto precisa estar no path.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from calculadora.carteira import (
    aplicar_compra,
    aplicar_venda,
    atualizar_proventos_carteira,
    calendario_proventos_carteira,
    desfazer_carteira,
    dy_estimado,
    enriquecer_posicoes,
    estimar_proventos_carteira,
    export_csv_carteira,
    garantir_acoes_das_posicoes,
    parse_csv_carteira,
    posicoes_de_editor,
    push_undo_carteira,
    rotulo_mes_curto,
    serie_patrimonio_aprox,
)
from calculadora.classes import (
    AnalisePrecoUnit,
    ClasseComparativo,
    analisar_preco_unit,
    comparar_classes,
    melhor_por_margem,
)
from calculadora.core import (
    BAZIN_DY,
    BaseMethod,
    ContaClassica,
    ValuationResult,
    YearCriterion,
    calculate,
    conta_bazin,
    conta_graham,
    resumo_datas_com,
)
from calculadora.empresa import EmpresaInfo, fetch_empresa
from calculadora.fundamentos import Fundamentos, fetch_fundamentos
from calculadora.preco import RANGES_HISTORICO, fetch_historico, fetch_preco, logo_url
from calculadora.proventos_fetch import fetch_proventos
from calculadora.chat import (
    MensagemChat,
    SUGESTOES_PADRAO,
    extrair_tickers_texto,
    montar_contexto_chat,
    pergunta_ideias_aporte,
    responder_chat,
)
from calculadora.gemini_client import gemini_api_key
from calculadora.mail import apply_runtime_secrets, smtp_config_from_env
from calculadora.noticias import (
    buscar_noticias,
    garantir_empresa_toml,
    ligar_noticias,
    load_empresas,
)
from calculadora.relatorio import (
    enviar_relatorio,
    gerar_relatorio,
    sentimento_rapido_ticker,
    _impacto_por_palavras,
)
from calculadora.setores import (
    agrupar_por_setor,
    rotulo_setor,
    setor_de_perfil,
    setor_do_ticker,
)
from calculadora.sinal import ItemFila, formatar_margem_pct, item_fila_de_salva
from calculadora.tese import IPCA_MAIS_PADRAO, SELIC_PADRAO, montar_tese_dividendo
from calculadora.storage import (
    AcaoSalva,
    excluir_acao,
    listar_acoes,
    listar_posicoes,
    obter_acao,
    salvar_acao,
    substituir_posicoes,
)
from calculadora.tab_bar import browser_tabs
from calculadora.ui_theme import (
    card_conta,
    card_decisao,
    card_listas,
    card_secao,
    chart_barras_horiz,
    chart_linha,
    chart_linhas,
    chart_pizza_setores,
    definir_tema,
    html_table,
    inject_css,
    inject_css_popover,
    metricas_grid,
    obter_tema,
    page_header,
    section_label,
    tom_de_decisao,
    tom_de_diferenca,
)

st.set_page_config(
    page_title="Preço teto por proventos",
    layout="wide",
    initial_sidebar_state="collapsed",
)

EXEMPLO = """Dividendo	14/08/2026	26/11/2026	0,08025458
JCP	14/08/2026	26/11/2026	0,11982486
JCP	11/05/2026	26/08/2026	0,18633271
Dividendo	29/04/2026	27/05/2026	0,25336381
Dividendo	29/04/2026	27/05/2026	0,05116205
JCP	14/11/2025	28/01/2026	0,13980241
Dividendo	14/11/2025	28/01/2026	0,17298440
JCP	18/08/2025	27/11/2025	0,21299312
Dividendo	18/08/2025	27/11/2025	0,07673069
JCP	12/05/2025	27/08/2025	0,18217415
Dividendo	29/04/2025	27/11/2025	0,10730153"""


def _default_acao() -> dict:
    return {"ticker": "", "preco": 0.0, "dy": 6.0, "proventos": ""}


def _init_state() -> None:
    if "acoes" not in st.session_state:
        st.session_state.acoes = [_default_acao()]
    if "active_tab" not in st.session_state:
        st.session_state.active_tab = 0
    st.session_state.auto_preco_ligado = True
    if "auto_preco_intervalo" not in st.session_state:
        st.session_state.auto_preco_intervalo = 30
    if "empresa_cache" not in st.session_state:
        st.session_state.empresa_cache = {}
    if "pagina_app" not in st.session_state:
        st.session_state.pagina_app = "Calculadora"
    if "tema_ui" not in st.session_state:
        qp = st.query_params.get("tema")
        if isinstance(qp, list):
            qp = qp[0] if qp else None
        st.session_state.tema_ui = qp if qp in ("claro", "escuro") else "claro"


def _obter_empresa(ticker: str) -> EmpresaInfo | None:
    """Busca (com cache de sessão) o perfil da empresa para o ticker."""
    t = str(ticker or "").strip().upper().removesuffix(".SA")
    if not t:
        return None
    cache: dict = st.session_state.setdefault("empresa_cache", {})
    if t in cache:
        return cache[t]
    try:
        info = fetch_empresa(t)
    except Exception:  # noqa: BLE001
        info = None
    cache[t] = info
    if info is not None:
        _registrar_setor_se_novo(t, info)
    return info


def _registrar_setor_se_novo(ticker: str, info: EmpresaInfo) -> None:
    """Grava no empresas.toml se a raiz ainda não existir (evita cair em Outros)."""
    setor = setor_de_perfil(info.setor, info.industria)
    try:
        criou = garantir_empresa_toml(ticker, nome=info.nome or "", setor=setor)
    except Exception:  # noqa: BLE001
        return
    if criou:
        st.session_state.pop("_empresas_mapa", None)
        st.session_state.pop("_empresas_mtime", None)


def _on_ticker_change(tab_i: int) -> None:
    """Ao mudar o ticker: descrição, preço, proventos e salvamento automático."""
    ticker = str(st.session_state.get(f"ticker_{tab_i}", "") or "").strip().upper()
    if not ticker:
        return

    cache: dict = st.session_state.setdefault("empresa_cache", {})
    if ticker not in cache or cache[ticker] is None:
        cache.pop(ticker, None)
        _obter_empresa(ticker)

    if ticker == "EXEMPLO":
        return

    # Preço ao vivo
    try:
        cot = fetch_preco(ticker)
        st.session_state[f"preco_{tab_i}"] = float(cot.preco)
        quando = cot.horario.strftime("%d/%m/%Y %H:%M") if cot.horario else "agora"
        st.session_state[f"preco_msg_{tab_i}"] = (
            f"Preço {cot.ticker}: R$ {cot.preco:.4f} ({cot.fonte}, {quando} BRT)"
        )
    except Exception as exc:  # noqa: BLE001
        st.session_state[f"preco_msg_{tab_i}"] = f"Preço: falha automática ({exc})"

    # Proventos: busca se vazio ou se o ticker da aba mudou
    ultimo = str(st.session_state.get(f"_auto_ticker_{tab_i}", "") or "")
    prov_atual = str(st.session_state.get(f"proventos_{tab_i}", "") or "").strip()
    precisa_prov = (not prov_atual) or (ultimo != ticker)
    if precisa_prov:
        try:
            got = fetch_proventos(ticker)
            st.session_state[f"proventos_{tab_i}"] = got.texto
            st.session_state[f"proventos_msg_{tab_i}"] = (
                f"{got.quantidade} provento(s) via {got.fonte}"
            )
            if got.avisos:
                st.session_state[f"proventos_avisos_{tab_i}"] = got.avisos[:8]
        except Exception as exc:  # noqa: BLE001
            st.session_state[f"proventos_msg_{tab_i}"] = (
                f"Proventos: falha automática ({exc})"
            )

    st.session_state[f"_auto_ticker_{tab_i}"] = ticker

    # Salva se já tiver dados mínimos
    _tentar_auto_salvar(
        tab_i,
        ticker=ticker,
        preco=float(st.session_state.get(f"preco_{tab_i}", 0) or 0),
        dy=float(st.session_state.get(f"dy_{tab_i}", 6) or 6),
        proventos=str(st.session_state.get(f"proventos_{tab_i}", "") or ""),
    )


def _tentar_auto_salvar(
    tab_i: int,
    *,
    ticker: str,
    preco: float,
    dy: float,
    proventos: str,
) -> None:
    """Grava no SQLite quando ticker + preço + proventos estão ok (sem spam)."""
    t = ticker.strip().upper()
    if not t or t == "EXEMPLO":
        return
    if preco <= 0 or not proventos.strip() or dy <= 0:
        return
    fp = f"{t}|{preco:.6f}|{dy:.4f}|{hash(proventos)}"
    if st.session_state.get(f"_saved_fp_{tab_i}") == fp:
        return
    try:
        salva = salvar_acao(ticker=t, preco=preco, dy=dy, proventos=proventos)
    except Exception as exc:  # noqa: BLE001
        st.session_state[f"save_msg_{tab_i}"] = f"Auto-salvar falhou: {exc}"
        return
    st.session_state[f"_saved_fp_{tab_i}"] = fp
    st.session_state[f"save_msg_{tab_i}"] = (
        f"Salvo automaticamente: {salva.ticker}"
    )


def _abrir_acao_salva(ticker: str) -> None:
    acao = obter_acao(ticker)
    if not acao:
        return
    _load_acoes_into_tabs(
        [
            {
                "ticker": acao.ticker,
                "preco": acao.preco,
                "dy": acao.dy,
                "proventos": acao.proventos,
            }
        ],
        replace=False,
    )
    _apply_pending_updates()


def _render_lista_salvas(salvas: list) -> None:
    """Selectbox: ações salvas + catálogo (empresas.toml), filtrável por digitação."""
    empresas = _empresas_mapa()
    grupos = agrupar_por_setor(
        salvas,
        setor_de=lambda a: setor_do_ticker(a.ticker, empresas),
        chave_item=lambda a: a.ticker,
    )
    opcoes: list[str] = []
    rotulos: dict[str, str] = {}
    for setor, grupo in grupos:
        for a in grupo:
            opcoes.append(a.ticker)
            rotulos[a.ticker] = f"{rotulo_setor(setor)} · {a.ticker}"

    salvos = {t.upper() for t in opcoes}
    for emp in sorted(
        empresas.values(),
        key=lambda e: (rotulo_setor(e.setor).lower(), e.ticker_preferencial),
    ):
        t = emp.ticker_preferencial
        if not t or t in salvos:
            continue
        opcoes.append(t)
        rotulos[t] = f"{rotulo_setor(emp.setor)} · {t} · {emp.nome_principal}"

    def _abrir_salva_callback() -> None:
        ticker = st.session_state.get("sidebar_abrir_ticker")
        if not ticker:
            return
        t = str(ticker).strip().upper()
        if st.session_state.get("pagina_app") != "Calculadora":
            st.session_state._pending_pagina = "Calculadora"
            st.session_state._pending_abrir_ticker_flex = t
        else:
            _abrir_acao_salva_ou_nova(t)

    escolhido = st.session_state.get("sidebar_abrir_ticker")
    logo = logo_url(str(escolhido)) if escolhido else ""

    c_logo, c_sel = st.columns([1, 8], vertical_alignment="bottom")
    with c_logo:
        if logo:
            st.markdown(
                f'<div style="padding-bottom:0.35rem">'
                f'<img src="{logo}" width="22" height="22" '
                f'style="border-radius:3px;object-fit:contain;background:#fff;'
                f'display:block;" '
                f'onerror="this.style.visibility=\'hidden\'" /></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="width:22px;height:22px;padding-bottom:0.35rem"></div>',
                unsafe_allow_html=True,
            )
    with c_sel:
        st.selectbox(
            "Buscar ticker",
            options=opcoes,
            index=None,
            format_func=lambda t: rotulos.get(t, t),
            placeholder="Digite para filtrar salvas ou catálogo…",
            key="sidebar_abrir_ticker",
            on_change=_abrir_salva_callback,
            help="Salvas e catálogo (ex.: ITUB4, Copasa). Ao escolher, abre a análise.",
        )
    st.caption(
        f"Salvas e catálogo · {len(salvas)} salva(s) · {len(grupos)} setor(es)"
    )


def _render_gestao_salvas_ajustes(salvas: list[AcaoSalva]) -> None:
    """Por setor / excluir / catálogo — só dentro do popover Ajustes."""
    empresas = _empresas_mapa()
    if salvas:
        grupos = agrupar_por_setor(
            salvas,
            setor_de=lambda a: setor_do_ticker(a.ticker, empresas),
            chave_item=lambda a: a.ticker,
        )
        with st.expander("Por setor (salvas)", expanded=False):
            for setor, grupo in grupos:
                tickers = ", ".join(a.ticker for a in grupo)
                st.markdown(f"**{rotulo_setor(setor)}** ({len(grupo)})  \n{tickers}")
        with st.expander("Excluir salva", expanded=False):
            tickers = [a.ticker for a in salvas]
            st.selectbox(
                "Ticker",
                options=tickers,
                key="sidebar_excluir_ticker",
                label_visibility="collapsed",
            )
            if st.button("Excluir selecionada", use_container_width=True):
                alvo = st.session_state.get("sidebar_excluir_ticker")
                if alvo and excluir_acao(alvo):
                    st.rerun()
    else:
        st.caption("Nenhuma ação salva ainda.")
    _sidebar_catalogo_empresas()


def _render_empresa(
    info: EmpresaInfo | None,
    *,
    ticker: str,
    proventos: str = "",
) -> None:
    if not ticker.strip():
        return
    logo = (info.logo_url if info else "") or logo_url(ticker)
    col_logo, col_txt = st.columns([1, 8], vertical_alignment="center")
    with col_logo:
        if logo:
            st.image(logo, width=56)
    with col_txt:
        if info is None:
            st.caption("Não encontrei descrição automática para este ticker.")
        else:
            st.markdown(f"**{info.resumo}**")
            if info.descricao:
                st.caption(info.descricao)
            st.caption(f"Fonte: {info.fonte}")
        datas = resumo_datas_com(proventos)
        if datas.proxima or datas.ultima:
            st.caption(
                f"Data-com · próxima: **{datas.fmt_proxima()}** · "
                f"última: **{datas.fmt_ultima()}**"
            )


def _obter_historico(ticker: str, periodo: str):
    cache: dict = st.session_state.setdefault("hist_cache", {})
    chave = f"{ticker.strip().upper()}:{periodo}"
    if chave in cache:
        return cache[chave]
    try:
        hist = fetch_historico(ticker, periodo=periodo)
    except Exception as exc:  # noqa: BLE001
        cache[chave] = exc
        return exc
    cache[chave] = hist
    return hist


def _render_grafico_preco(ticker: str, tab_i: int) -> None:
    t = ticker.strip().upper()
    if not t or t == "EXEMPLO":
        return

    st.markdown("##### Evolução do preço")
    opcoes = list(RANGES_HISTORICO.keys())
    periodo = st.selectbox(
        "Período",
        options=opcoes,
        index=opcoes.index("1y") if "1y" in opcoes else 0,
        format_func=lambda k: RANGES_HISTORICO[k],
        key=f"hist_range_{tab_i}",
    )
    resultado = _obter_historico(t, periodo)
    if isinstance(resultado, Exception):
        st.caption(f"Não foi possível carregar o gráfico: {resultado}")
        return

    df = pd.DataFrame(
        {"Preço (R$)": [p.preco for p in resultado.pontos]},
        index=pd.to_datetime([p.data for p in resultado.pontos]),
    )
    chart_linha(df, y_col="Preço (R$)", height=240)
    primeiro = resultado.pontos[0]
    ultimo = resultado.pontos[-1]
    var = (ultimo.preco / primeiro.preco - 1.0) * 100.0 if primeiro.preco else 0.0
    st.caption(
        f"{primeiro.data.strftime('%d/%m/%Y')}: R$ {primeiro.preco:.2f} → "
        f"{ultimo.data.strftime('%d/%m/%Y')}: R$ {ultimo.preco:.2f} "
        f"({var:+.1f}%) · {resultado.fonte}"
    )


def _atualizar_precos_abertas() -> tuple[int, list[str]]:
    """Atualiza o preço de todas as abas com ticker válido. Devolve (ok, erros)."""
    ok = 0
    erros: list[str] = []
    for i, acao in enumerate(st.session_state.acoes):
        ticker = str(
            st.session_state.get(f"ticker_{i}", acao.get("ticker", "")) or ""
        ).strip().upper()
        if not ticker or ticker == "EXEMPLO":
            continue
        try:
            cot = fetch_preco(ticker)
            st.session_state.acoes[i]["preco"] = float(cot.preco)
            st.session_state.acoes[i]["ticker"] = ticker
            ok += 1
        except Exception as exc:  # noqa: BLE001
            erros.append(f"{ticker}: {exc}")
    return ok, erros


def _rodar_auto_precos() -> None:
    """
    Fragmento que, com a opção ligada, busca preços periodicamente
    e recarrega a tela para recalcular o teto.
    """
    ligado = bool(st.session_state.get("auto_preco_ligado"))
    intervalo = int(st.session_state.get("auto_preco_intervalo", 30))
    run_every = timedelta(seconds=intervalo) if ligado else None

    @st.fragment(run_every=run_every)
    def _tick() -> None:
        if not st.session_state.get("auto_preco_ligado"):
            return

        agora = time.time()
        intervalo_local = int(st.session_state.get("auto_preco_intervalo", 30))
        ultimo = float(st.session_state.get("_last_auto_fetch", 0) or 0)
        # Evita loop infinito: após st.rerun() o fragment remonta na hora
        if agora - ultimo < max(5.0, intervalo_local * 0.5):
            status = st.session_state.get("auto_preco_status")
            if status:
                st.caption(status)
            return

        st.session_state._last_auto_fetch = agora
        n_ok, erros = _atualizar_precos_abertas()
        horario = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%H:%M:%S")
        if n_ok == 0 and not erros:
            st.session_state.auto_preco_status = (
                f"Auto-preço: nenhuma aba com ticker válido ({horario})"
            )
            st.caption(st.session_state.auto_preco_status)
            return

        for i, acao in enumerate(st.session_state.acoes):
            _queue_update(f"preco_{i}", float(acao.get("preco") or 0.0))

        status = f"Auto-preço: {n_ok} atualizada(s) às {horario}"
        if erros:
            status += f" | falhas: {'; '.join(erros[:3])}"
        st.session_state.auto_preco_status = status
        st.rerun()

    _tick()


def _empresas_mapa() -> dict:
    from calculadora.noticias import DEFAULT_EMPRESAS

    path = DEFAULT_EMPRESAS
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    if (
        st.session_state.get("_empresas_mtime") == mtime
        and st.session_state.get("_empresas_mapa") is not None
    ):
        return st.session_state._empresas_mapa
    mapa = load_empresas()
    st.session_state._empresas_mapa = mapa
    st.session_state._empresas_mtime = mtime
    return mapa


def _preencher_fund_cache(tickers: list[str]) -> int:
    """
    Busca LPA/VPA em paralelo para Graham e grava em fund_cache.
    Retorna quantos tickers novos foram consultados.
    """
    cache: dict = st.session_state.setdefault("fund_cache", {})
    faltam = []
    for raw in tickers:
        t = str(raw or "").strip().upper().removesuffix(".SA")
        if t and t not in cache:
            faltam.append(t)
    if not faltam:
        return 0

    def _um(t: str) -> tuple[str, Fundamentos | None]:
        try:
            return t, fetch_fundamentos(t, timeout=12.0)
        except Exception:  # noqa: BLE001
            return t, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for ticker, fund in pool.map(_um, faltam):
            cache[ticker] = fund
    # Invalida fila para recalcular com Graham
    st.session_state.pop("_fila_fp", None)
    st.session_state.pop("_fila_itens", None)
    return len(faltam)


def _itens_fila_salvas(salvas: list[AcaoSalva]) -> list[ItemFila]:
    """Status rápido das ações salvas (Bazin local; Graham via fund_cache)."""
    fund_cache: dict = st.session_state.get("fund_cache") or {}
    empresas = _empresas_mapa()
    fp_parts = []
    for a in salvas:
        fund = fund_cache.get(a.ticker.upper())
        lpa = getattr(fund, "lpa", None) if fund else None
        vpa = getattr(fund, "vpa", None) if fund else None
        setor = setor_do_ticker(a.ticker, empresas)
        fp_parts.append(
            f"v2|{a.ticker}|{a.preco:.4f}|{a.dy:.2f}|{hash(a.proventos)}|{lpa}|{vpa}|{setor}"
        )
    fp = "|".join(fp_parts)
    if st.session_state.get("_fila_fp") == fp:
        cached = st.session_state.get("_fila_itens")
        if isinstance(cached, list):
            return cached

    itens: list[ItemFila] = []
    for a in salvas:
        fund = fund_cache.get(a.ticker.upper())
        lpa = getattr(fund, "lpa", None) if fund else None
        vpa = getattr(fund, "vpa", None) if fund else None
        cfg_ui = st.session_state.get("_calc_sidebar_cfg") or {}
        itens.append(
            item_fila_de_salva(
                ticker=a.ticker,
                preco=a.preco,
                dy=a.dy,
                proventos=a.proventos,
                lpa=lpa,
                vpa=vpa,
                setor=setor_do_ticker(a.ticker, empresas),
                fund=fund if isinstance(fund, Fundamentos) else None,
                selic=float(cfg_ui.get("selic", SELIC_PADRAO)),
                ipca_mais=float(cfg_ui.get("ipca_mais", IPCA_MAIS_PADRAO)),
            )
        )
    # Mantém ordem por setor na fila
    ordenados: list[ItemFila] = []
    for _, grupo in agrupar_por_setor(
        itens, setor_de=lambda i: i.setor, chave_item=lambda i: i.ticker
    ):
        ordenados.extend(grupo)
    st.session_state._fila_fp = fp
    st.session_state._fila_itens = ordenados
    return ordenados


def _chip_html(it: ItemFila) -> str:
    return (
        f'<span class="fila-chip" title="{html.escape(it.sinal.resumo, quote=True)}">'
        f'<span class="fila-dot" style="background:{it.sinal.cor}"></span>'
        f"{html.escape(it.texto)}</span>"
    )


def _grupos_fila(salvas: list[AcaoSalva]) -> list[tuple[str, list[ItemFila]]]:
    itens = _itens_fila_salvas(salvas)
    if not itens:
        return []
    return agrupar_por_setor(
        itens, setor_de=lambda i: i.setor, chave_item=lambda i: i.ticker
    )


def _render_fila_marquee(grupos: list[tuple[str, list[ItemFila]]]) -> None:
    """Faixa rolante com status das ações, agrupada por setor."""
    if not grupos:
        return
    chips: list[str] = []
    for setor, grupo in grupos:
        chips.append(f'<span class="fila-setor">{html.escape(rotulo_setor(setor))}</span>')
        chips.extend(_chip_html(it) for it in grupo)
    faixa = "".join(chips)
    # ~14s por item, mínimo 160s — mais tickers = mais lento, legível
    duracao_s = max(160, len(chips) * 14)
    markup = (
        f'<div class="fila-acoes" aria-label="Fila de status das ações salvas por setor">'
        f'<div class="fila-track" style="animation-duration:{duracao_s}s">'
        f"{faixa}{faixa}</div></div>"
    )
    st.markdown(markup, unsafe_allow_html=True)
    st.caption(
        "Fila por setor · Comprar / Esperar / Evitar. "
        "Passe o mouse para pausar."
    )


def _ir_para_calculadora(ticker: str) -> None:
    """
    Agenda ir para a Calculadora com o ticker aberto.

    Não altera ``pagina_app`` diretamente (o radio da sidebar usa essa chave).
    A troca é aplicada no início do próximo run, antes de criar o widget.
    """
    t = str(ticker or "").strip().upper()
    if not t:
        return
    st.session_state._pending_pagina = "Calculadora"
    st.session_state._pending_abrir_ticker_flex = t


def _consumir_query_abrir() -> None:
    """Link ``?abrir=TICKER`` nas tabelas → abre na Calculadora."""
    try:
        raw = st.query_params.get("abrir")
    except Exception:  # noqa: BLE001
        return
    if not raw:
        return
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ""
    t = str(raw or "").strip().upper()
    if not t:
        return
    try:
        st.query_params.clear()
    except Exception:  # noqa: BLE001
        pass
    st.session_state._pending_pagina = "Calculadora"
    st.session_state._pending_abrir_ticker_flex = t


def _aplicar_navegacao_pendente() -> None:
    """Aplica troca de página / abertura de ticker antes de criar widgets."""
    pending = st.session_state.pop("_pending_pagina", None)
    if pending in ("Calculadora", "Por setor", "Carteira", "Assistente"):
        st.session_state.pagina_app = pending
    ticker = st.session_state.pop("_pending_abrir_ticker", None)
    if ticker:
        _abrir_acao_salva(str(ticker))
    flex = st.session_state.pop("_pending_abrir_ticker_flex", None)
    if flex:
        _abrir_acao_salva_ou_nova(str(flex))


def _render_pagina_setores(salvas: list[AcaoSalva]) -> None:
    """Página dedicada: visão da carteira por setor."""
    page_header(
        "Por setor",
        "Leitura vs teto Bazin/Graham nas ações salvas. "
        "Margens absurdas aparecem como “muito acima/abaixo” e não forçam o sinal.",
    )
    if not salvas:
        st.info("Nenhuma ação salva ainda. Cadastre tickers na Calculadora.")
        return

    # Graham precisa de LPA/VPA — na Calculadora isso só vinha se a aba já tivesse
    # buscado fundamentos; aqui buscamos em lote para a carteira inteira.
    with st.spinner("Buscando LPA/VPA (Graham) das ações salvas…"):
        n_buscas = _preencher_fund_cache([a.ticker for a in salvas])
    if n_buscas:
        st.caption(f"Fundamentos atualizados para {n_buscas} ticker(s).")

    grupos = _grupos_fila(salvas)
    _render_fila_marquee(grupos)

    # Atalho para abrir na calculadora
    todos = [it.ticker for _, g in grupos for it in g]
    c1, c2 = st.columns([3, 1])
    with c1:
        escolher = st.selectbox(
            "Abrir na calculadora",
            options=todos,
            index=None,
            placeholder="Escolha um ticker…",
            key="setor_abrir_ticker",
        )
    with c2:
        st.write("")  # alinha o botão
        st.write("")
        st.button(
            "Abrir",
            use_container_width=True,
            disabled=not escolher,
            on_click=_ir_para_calculadora,
            args=(str(escolher),) if escolher else ("",),
            key="btn_setor_abrir_calc",
        )

    for setor, grupo in grupos:
        contagem = {
            "comprar": sum(1 for i in grupo if i.sinal.codigo == "compra"),
            "esperar": sum(1 for i in grupo if i.sinal.codigo == "aguardar"),
            "evitar": sum(1 for i in grupo if i.sinal.codigo == "cautela"),
        }
        bits = [f"{k} {v}" for k, v in contagem.items() if v]
        st.subheader(
            f"{rotulo_setor(setor)} · {len(grupo)}"
            + (f" · {', '.join(bits)}" if bits else "")
        )
        html_table(
            ["Ticker", "Preço", "Próx. com", "Últ. com", "Bazin", "Graham", "Leitura"],
            [
                [
                    it.ticker,
                    (
                        f"{it.preco:.2f}".replace(".", ",")
                        if it.preco and it.preco > 0
                        else "—"
                    ),
                    it.datas_com.fmt_proxima(),
                    it.datas_com.fmt_ultima(),
                    formatar_margem_pct(it.pct_bazin),
                    formatar_margem_pct(it.pct_graham),
                    it.sinal.rotulo,
                ]
                for it in grupo
            ],
            numeric_cols=[1, 4, 5],
            link_cols={0: "abrir"},
        )
    st.caption(
        "Clique no ticker para abrir na Calculadora. "
        "Data-com vem dos proventos salvos (próxima futura ou última já passada). "
        "Graham usa LPA/VPA — “—” se a fonte não devolveu dados positivos."
    )


def _peso_setor_na_carteira(ticker: str) -> tuple[float | None, str, str]:
    """Retorna (peso do setor nas posições, chave do setor, rótulo)."""
    from calculadora.carteira import enriquecer_posicoes

    pos = listar_posicoes()
    if not pos:
        return None, "", ""
    empresas = _empresas_mapa()
    setor = setor_do_ticker(ticker, empresas)
    precos: dict[str, float | None] = {}
    for p in pos:
        salva = obter_acao(p.ticker)
        precos[p.ticker] = float(salva.preco) if salva and salva.preco > 0 else p.preco_medio
    setores = {p.ticker: setor_do_ticker(p.ticker, empresas) for p in pos}
    enriq = enriquecer_posicoes(pos, precos, setores=setores)
    peso = sum(e.peso_pct or 0.0 for e in enriq if e.setor == setor)
    return (peso if peso > 0 else None), setor, rotulo_setor(setor)


def _noticias_cacheadas(*, horas: int = 36):
    """RSS em cache de sessão (TTL 30 min) para sentimento na Calculadora."""
    agora = time.time()
    cache = st.session_state.get("_noticias_cache")
    if (
        isinstance(cache, dict)
        and cache.get("noticias") is not None
        and agora - float(cache.get("ts") or 0) < 1800
    ):
        return cache["noticias"]
    try:
        noticias, _avisos = buscar_noticias(horas=horas, timeout=12.0)
    except Exception:  # noqa: BLE001
        noticias = []
    st.session_state._noticias_cache = {"ts": agora, "noticias": noticias}
    return noticias


def _sentimento_ticker_calc(ticker: str) -> tuple[str | None, bool]:
    """Sentimento direto (cache RSS) para a tese da Calculadora."""
    t = str(ticker or "").strip().upper().removesuffix(".SA")
    if not t or t == "EXEMPLO":
        return None, False
    noticias = list(_noticias_cacheadas())
    if not noticias:
        return None, False
    empresas = _empresas_mapa()
    ligar_noticias(noticias, [t], empresas)
    sentimento, confiante = sentimento_rapido_ticker(t, noticias)
    return (sentimento if confiante else None), confiante


def _obter_bazin_graham(
    result: ValuationResult,
) -> tuple[ContaClassica | None, ContaClassica | None, float | None, float | None]:
    baz: ContaClassica | None = None
    gra: ContaClassica | None = None
    pct_bazin: float | None = None
    pct_graham: float | None = None
    if result.base is not None and result.base > 0 and result.preco_atual > 0:
        baz = conta_bazin(result.base, result.preco_atual)
        pct_bazin = baz.diferenca
    fund = _obter_fundamentos(result.ticker)
    if (
        fund
        and fund.lpa is not None
        and fund.vpa is not None
        and fund.lpa > 0
        and fund.vpa > 0
        and result.preco_atual > 0
    ):
        try:
            gra = conta_graham(fund.lpa, fund.vpa, result.preco_atual)
            pct_graham = gra.diferenca
        except Exception:  # noqa: BLE001
            gra = None
    return baz, gra, pct_bazin, pct_graham


def _commit_active_to_acoes() -> None:
    """Grava os widgets da aba ativa em st.session_state.acoes (fonte da verdade)."""
    i = int(st.session_state.get("active_tab", 0))
    acoes = st.session_state.acoes
    if i < 0 or i >= len(acoes):
        return
    acoes[i] = {
        "ticker": str(st.session_state.get(f"ticker_{i}", acoes[i].get("ticker", "")) or "").strip(),
        "preco": float(st.session_state.get(f"preco_{i}", acoes[i].get("preco", 0.0)) or 0.0),
        "dy": float(st.session_state.get(f"dy_{i}", acoes[i].get("dy", 6.0)) or 6.0),
        "proventos": str(
            st.session_state.get(f"proventos_{i}", acoes[i].get("proventos", "")) or ""
        ),
    }


def _hydrate_tab(i: int) -> None:
    """Copia acoes[i] para as chaves dos widgets (antes de criá-los)."""
    if i < 0 or i >= len(st.session_state.acoes):
        return
    acao = st.session_state.acoes[i]
    _queue_update(f"ticker_{i}", acao.get("ticker", ""))
    _queue_update(f"preco_{i}", float(acao.get("preco") or 0.0))
    _queue_update(f"dy_{i}", float(acao.get("dy") or 6.0))
    _queue_update(f"proventos_{i}", acao.get("proventos", ""))


def _snapshot_acoes_from_widgets() -> list[dict]:
    """
    Atualiza só a aba ativa a partir dos widgets.
    Abas inativas mantêm o que já está em acoes (evita zerar ao trocar).
    """
    active = int(st.session_state.get("active_tab", 0))
    out: list[dict] = []
    for i, base in enumerate(st.session_state.acoes):
        if i == active:
            out.append(
                {
                    "ticker": str(
                        st.session_state.get(f"ticker_{i}", base.get("ticker", "")) or ""
                    ).strip(),
                    "preco": float(
                        st.session_state.get(f"preco_{i}", base.get("preco", 0.0)) or 0.0
                    ),
                    "dy": float(
                        st.session_state.get(f"dy_{i}", base.get("dy", 6.0)) or 6.0
                    ),
                    "proventos": str(
                        st.session_state.get(f"proventos_{i}", base.get("proventos", ""))
                        or ""
                    ),
                }
            )
        else:
            out.append(
                {
                    "ticker": str(base.get("ticker", "") or "").strip(),
                    "preco": float(base.get("preco") or 0.0),
                    "dy": float(base.get("dy") or 6.0),
                    "proventos": str(base.get("proventos") or ""),
                }
            )
    return out


def _rebind_acao_widgets(acoes: list[dict]) -> None:
    """Limpa chaves antigas e reaplica widgets na ordem nova."""
    _clear_acao_widgets(max(len(acoes) + 3, 5))
    st.session_state.acoes = acoes or [_default_acao()]
    for i, acao in enumerate(st.session_state.acoes):
        _queue_update(f"ticker_{i}", acao.get("ticker", ""))
        _queue_update(f"preco_{i}", float(acao.get("preco") or 0.0))
        _queue_update(f"dy_{i}", float(acao.get("dy") or 6.0))
        _queue_update(f"proventos_{i}", acao.get("proventos", ""))


def _fechar_aba(idx: int) -> None:
    """Fecha uma aba qualquer e reindexa widgets."""
    _commit_active_to_acoes()
    dados = [dict(a) for a in st.session_state.acoes]
    if len(dados) <= 1:
        _rebind_acao_widgets([_default_acao()])
        st.session_state.active_tab = 0
        return

    if idx < 0 or idx >= len(dados):
        return

    dados.pop(idx)
    _rebind_acao_widgets(dados)

    active = int(st.session_state.get("active_tab", 0))
    if active > idx:
        active -= 1
    if active >= len(dados):
        active = len(dados) - 1
    st.session_state.active_tab = max(0, active)


def _render_tab_bar() -> None:
    """Barra de abas compacta, estilo navegador, com ✕ no hover."""
    n = len(st.session_state.acoes)
    if st.session_state.active_tab >= n:
        st.session_state.active_tab = max(0, n - 1)

    # Rótulos vêm de acoes (fonte da verdade), não dos widgets inativos
    labels = [
        str(a.get("ticker", "") or "").strip() or f"Ação {i + 1}"
        for i, a in enumerate(st.session_state.acoes)
    ]
    logos = [
        logo_url(str(a.get("ticker", "") or ""))
        for a in st.session_state.acoes
    ]

    event = browser_tabs(
        labels,
        logos=logos,
        active=int(st.session_state.active_tab),
        tema=obter_tema(),
        key="browser_tabs",
    )
    if not event:
        return

    event_id = event.get("id")
    if event_id and event_id == st.session_state.get("_last_tab_event"):
        return
    if event_id:
        st.session_state._last_tab_event = event_id

    action = event.get("action")
    idx = int(event.get("index", -1))
    if action == "select" and 0 <= idx < n:
        if idx != st.session_state.active_tab:
            _commit_active_to_acoes()
            st.session_state.active_tab = idx
            _hydrate_tab(idx)
            st.rerun()
    elif action == "close" and 0 <= idx < n and n > 1:
        _fechar_aba(idx)
        st.rerun()


def _apply_pending_updates() -> None:
    """
    Aplica atualizações de widgets ANTES de criá-los.
    Evita StreamlitAPIException ao gravar session_state após o widget existir.
    """
    pending = st.session_state.pop("pending_updates", None) or {}
    for key, value in pending.items():
        st.session_state[key] = value


def _queue_update(key: str, value) -> None:
    pending = st.session_state.setdefault("pending_updates", {})
    pending[key] = value


def _clear_acao_widgets(n: int) -> None:
    for i in range(n + 5):  # folga para abas removidas
        for prefix in (
            "ticker_",
            "preco_",
            "dy_",
            "proventos_",
            "exemplo_",
            "reload_",
            "proventos_msg_",
            "proventos_avisos_",
            "hist_range_",
        ):
            st.session_state.pop(f"{prefix}{i}", None)


def _load_acoes_into_tabs(novas: list[dict], *, replace: bool = False) -> None:
    """Abre ações nas abas (substitui ou acrescenta sem duplicar ticker)."""
    _commit_active_to_acoes()
    if replace:
        _clear_acao_widgets(len(st.session_state.acoes))
        st.session_state.acoes = novas or [_default_acao()]
    else:
        abertos = {
            str(a.get("ticker", "")).strip().upper()
            for a in st.session_state.acoes
            if str(a.get("ticker", "")).strip()
        }
        for acao in novas:
            ticker = str(acao.get("ticker", "")).strip().upper()
            if ticker and ticker in abertos:
                # Atualiza a aba já aberta com os dados salvos
                for i, atual in enumerate(st.session_state.acoes):
                    if str(atual.get("ticker", "")).strip().upper() == ticker:
                        st.session_state.acoes[i] = acao
                        break
            else:
                # Substitui primeira aba vazia, se houver
                replaced = False
                for i, atual in enumerate(st.session_state.acoes):
                    if not str(atual.get("ticker", "")).strip() and not str(
                        atual.get("proventos", "")
                    ).strip():
                        st.session_state.acoes[i] = acao
                        replaced = True
                        break
                if not replaced:
                    st.session_state.acoes.append(acao)
                if ticker:
                    abertos.add(ticker)

    for i, acao in enumerate(st.session_state.acoes):
        _queue_update(f"ticker_{i}", acao.get("ticker", ""))
        _queue_update(f"preco_{i}", float(acao.get("preco") or 0.0))
        _queue_update(f"dy_{i}", float(acao.get("dy") or 6.0))
        _queue_update(f"proventos_{i}", acao.get("proventos", ""))

    # Foca na última ação pedida para abrir
    if novas:
        alvo = str(novas[-1].get("ticker", "")).strip().upper()
        for i, acao in enumerate(st.session_state.acoes):
            if str(acao.get("ticker", "")).strip().upper() == alvo:
                st.session_state.active_tab = i
                break
        else:
            st.session_state.active_tab = len(st.session_state.acoes) - 1
    elif st.session_state.acoes:
        st.session_state.active_tab = len(st.session_state.acoes) - 1


def _obter_fundamentos(ticker: str) -> Fundamentos | None:
    t = str(ticker or "").strip().upper().removesuffix(".SA")
    if not t or t == "EXEMPLO":
        return None
    cache: dict = st.session_state.setdefault("fund_cache", {})
    if t in cache:
        return cache[t]
    try:
        info = fetch_fundamentos(t)
    except Exception:  # noqa: BLE001
        info = None
    cache[t] = info
    return info


def _render_conta_card(conta: ContaClassica) -> None:
    card_conta(
        conta.nome,
        conta.veredito,
        tom_de_diferenca(conta.diferenca),
        [
            ("Preço justo", _fmt_money(conta.preco_justo)),
            ("Preço atual", _fmt_money(conta.preco_atual)),
            ("% vs justo", _fmt_pct(conta.diferenca)),
        ],
        detalhe=conta.detalhe or "",
    )


def _render_valuation_row(
    result: ValuationResult,
    baz: ContaClassica | None,
    gra: ContaClassica | None,
) -> None:
    """Faixa Bazin | Graham."""
    section_label("Valuation")
    col_b, col_g = st.columns(2)
    with col_b:
        if baz is not None:
            _render_conta_card(baz)
            if abs(result.dy_desejado - BAZIN_DY) > 1e-9:
                st.caption(
                    f"Seu DY: {_fmt_pct(result.dy_desejado)} · "
                    f"Bazin fixo {_fmt_pct(BAZIN_DY)}"
                )
        else:
            card_secao("Bazin", meta="Precisa de base de proventos e preço.")
    with col_g:
        if gra is not None:
            _render_conta_card(gra)
            fund = _obter_fundamentos(result.ticker)
            if fund and fund.lpa is not None and fund.vpa is not None:
                st.caption(
                    f"LPA {fund.lpa:.4f} · VPA {fund.vpa:.4f} · {fund.fonte}"
                )
        else:
            card_secao("Graham", meta="LPA/VPA indisponíveis ou inválidos.")


def _render_analise_preco_unit(analise: AnalisePrecoUnit) -> None:
    """Unit (11) vs soma dos componentes ON/PN avulsos."""
    st.markdown("**Preço final: unit vs montar separado**")
    formula = " + ".join(
        f"{qtd}×{tick} ({_fmt_money(preco)})"
        for tick, qtd, preco, _ in analise.componentes
    )
    st.caption(f"Composição: {formula}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Preço da unit", _fmt_money(analise.preco_unit))
    c2.metric("Soma avulsos", _fmt_money(analise.soma_componentes))
    c3.metric("Diferença R$", _fmt_money(analise.diff_reais))
    c4.metric("% unit vs avulsos", _fmt_pct(analise.pct_diff))

    if analise.pct_diff < -0.001:
        st.success(analise.veredito)
    elif analise.pct_diff > 0.001:
        st.warning(analise.veredito)
    else:
        st.info(analise.veredito)

    html_table(
        ["Componente", "Qtd", "Preço", "Subtotal"],
        [
            [
                tick,
                qtd,
                f"{preco:.4f}".replace(".", ","),
                f"{sub:.4f}".replace(".", ","),
            ]
            for tick, qtd, preco, sub in analise.componentes
        ],
        numeric_cols=[1, 2, 3],
    )


def _fmt_resumo_melhor(rotulo: str, melhor: ClasseComparativo | None, pct: float | None, atual: str) -> str | None:
    if melhor is None or pct is None:
        return None
    marca = " ← aberta" if melhor.ticker == atual else ""
    return f"**{rotulo}:** {melhor.ticker}{marca} ({melhor.tipo}) — {pct * 100:.1f}% vs teto"


def _obter_comparativo_classes(
    ticker: str,
    *,
    dy_desejado: float,
    n_ultimos: int | None,
    anos_especificos: list[int] | None,
    metodo: BaseMethod,
    criterio: YearCriterion,
    incluir_ano_andamento: bool,
    proventos_conhecidos: dict[str, str],
) -> list[ClasseComparativo]:
    cache: dict = st.session_state.setdefault("classes_cache", {})
    chave = (
        f"{ticker.upper()}|{dy_desejado}|{n_ultimos}|{anos_especificos}|"
        f"{metodo.value}|{criterio.value}|{incluir_ano_andamento}"
    )
    if chave in cache:
        return cache[chave]
    try:
        with st.spinner("Comparando classes ON/PN/Unit…"):
            linhas = comparar_classes(
                ticker,
                dy_desejado=dy_desejado,
                n_ultimos=n_ultimos,
                anos_especificos=anos_especificos,
                metodo=metodo,
                criterio=criterio,
                incluir_ano_andamento=incluir_ano_andamento,
                proventos_conhecidos=proventos_conhecidos,
            )
    except Exception as exc:  # noqa: BLE001
        st.caption(f"Comparativo de classes indisponível: {exc}")
        linhas = []
    cache[chave] = linhas
    return linhas


def _render_comparativo_classes(
    ticker: str,
    *,
    dy_desejado: float,
    n_ultimos: int | None,
    anos_especificos: list[int] | None,
    metodo: BaseMethod,
    criterio: YearCriterion,
    incluir_ano_andamento: bool,
    proventos_conhecidos: dict[str, str],
) -> None:
    linhas = _obter_comparativo_classes(
        ticker,
        dy_desejado=dy_desejado,
        n_ultimos=n_ultimos,
        anos_especificos=anos_especificos,
        metodo=metodo,
        criterio=criterio,
        incluir_ano_andamento=incluir_ano_andamento,
        proventos_conhecidos=proventos_conhecidos,
    )
    if len(linhas) < 2:
        return

    section_label("Qual classe comprar? (3 / 4 / 11)")
    st.caption(
        "Mesma empresa, classes diferentes. Compare por **seu DY**, **Bazin** e **Graham**. "
        "Valores % negativos = mais barato que o teto daquele critério. "
        "Para unit (11), veja também o bloco de preço final abaixo."
    )

    atual = ticker.strip().upper()
    melhor_bazin = melhor_por_margem(linhas, "pct_bazin")
    melhor_graham = melhor_por_margem(linhas, "pct_graham")
    melhor_usuario = melhor_por_margem(linhas, "pct_usuario")

    resumos = [
        r
        for r in (
            _fmt_resumo_melhor("Bazin (proventos ÷ 6%)", melhor_bazin, melhor_bazin.pct_bazin if melhor_bazin else None, atual),
            _fmt_resumo_melhor("Graham", melhor_graham, melhor_graham.pct_graham if melhor_graham else None, atual),
            _fmt_resumo_melhor(f"Seu DY ({dy_desejado:.1f}%)", melhor_usuario, melhor_usuario.pct_usuario if melhor_usuario else None, atual),
        )
        if r
    ]
    if resumos:
        st.markdown(" · ".join(resumos))

    def _n(v: float | None, casas: int = 2) -> str:
        if v is None:
            return "—"
        return f"{v:.{casas}f}".replace(".", ",")

    tabela_rows: list[list[object]] = []
    for c in linhas:
        marca = " ←" if c.ticker == atual else ""
        tabela_rows.append(
            [
                f"{c.ticker}{marca}",
                c.tipo,
                _n(c.preco, 4),
                _n(c.dy_atual * 100 if c.dy_atual is not None else None),
                _n(c.pct_usuario * 100 if c.pct_usuario is not None else None),
                _n(c.teto_usuario, 4),
                _n(c.pct_bazin * 100 if c.pct_bazin is not None else None),
                _n(c.teto_bazin, 4),
                _n(c.pct_graham * 100 if c.pct_graham is not None else None),
                _n(c.teto_graham, 4),
            ]
        )
    html_table(
        [
            "Ticker",
            "Tipo",
            "Preço",
            "DY %",
            "% seu DY",
            "Teto DY",
            "% Bazin",
            "Teto Bazin",
            "% Graham",
            "Teto Graham",
        ],
        tabela_rows,
        numeric_cols=[2, 3, 4, 5, 6, 7, 8, 9],
    )

    try:
        unit_analise = analisar_preco_unit(linhas)
    except Exception:  # noqa: BLE001
        unit_analise = None
    if unit_analise is not None:
        _render_analise_preco_unit(unit_analise)

    # Atalhos para abrir outra classe
    outras = [c for c in linhas if c.ticker != atual]
    if outras:
        cols = st.columns(min(len(outras), 4))
        for col, c in zip(cols, outras):
            with col:
                if st.button(
                    f"Abrir {c.ticker}",
                    key=f"abrir_classe_{atual}_{c.ticker}",
                    use_container_width=True,
                ):
                    prov = ""
                    try:
                        got = fetch_proventos(c.ticker)
                        prov = got.texto
                    except Exception:  # noqa: BLE001
                        pass
                    dy_pct = float(st.session_state.get(f"dy_{st.session_state.active_tab}", 6) or 6)
                    _load_acoes_into_tabs(
                        [
                            {
                                "ticker": c.ticker,
                                "preco": c.preco,
                                "dy": dy_pct,
                                "proventos": prov,
                            }
                        ],
                        replace=False,
                    )
                    st.rerun()


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.2f}%".replace(".", ",")


def _render_proventos_historico(result: ValuationResult) -> None:
    """Tabela de anos + meses (secundário, após decisão/valuation)."""
    section_label("Proventos")
    st.caption(
        "DY estimado = base líquida (média dos anos) ÷ preço atual — "
        "não é o DY que a empresa garante no próximo ano."
    )
    if result.anos_resumo:
        selecionados = set(result.anos_selecionados)
        rows: list[list[object]] = []
        for r in reversed(result.anos_resumo):
            ano_txt = f"{r.ano}"
            if r.parcial:
                ano_txt += " (parcial)"
            rows.append(
                [
                    ano_txt,
                    f"{r.bruto:.4f}".replace(".", ","),
                    f"{r.ir:.4f}".replace(".", ","),
                    f"{r.liquido:.4f}".replace(".", ","),
                    "✓" if r.ano in selecionados else "—",
                ]
            )
        html_table(
            ["Ano", "Bruto", "IR (JCP)", "Líquido", "Na base"],
            rows,
            numeric_cols=[1, 2, 3],
        )
    _render_meses_pagamento(result)


def _render_resultado(
    result: ValuationResult,
    *,
    n_ultimos: int | None = 5,
    anos_especificos: list[int] | None = None,
    metodo: BaseMethod = BaseMethod.MEDIA,
    criterio: YearCriterion = YearCriterion.DATA_COM,
    incluir_ano_andamento: bool = False,
    proventos_conhecidos: dict[str, str] | None = None,
) -> None:
    """Hierarquia: tese|teto → valuation → proventos → classes (contexto vem antes, no main)."""
    if result.erros:
        for err in result.erros:
            st.warning(err)

    if result.aviso:
        st.info(result.aviso)

    if result.veredito in ("Erro", "Sem dados"):
        card_decisao(
            result.veredito,
            result.aviso or "Não foi possível calcular o preço teto.",
            "bad",
            kicker="Resultado",
        )
        if result.meses_pagamento:
            _render_meses_pagamento(result)
        return

    cfg_ui = st.session_state.get("_calc_sidebar_cfg") or {}
    i_tab = int(st.session_state.get("active_tab", 0) or 0)
    prov_tab = str(st.session_state.get(f"proventos_{i_tab}", "") or "")
    selic = float(cfg_ui.get("selic", SELIC_PADRAO))
    ipca_mais = float(cfg_ui.get("ipca_mais", IPCA_MAIS_PADRAO))

    baz, gra, pct_bazin, pct_graham = _obter_bazin_graham(result)
    fund = _obter_fundamentos(result.ticker)
    peso, setor_chave, setor_rot = _peso_setor_na_carteira(result.ticker)
    if not setor_chave:
        setor_chave = setor_do_ticker(result.ticker, _empresas_mapa())
        setor_rot = rotulo_setor(setor_chave)
    sentimento, sent_ok = _sentimento_ticker_calc(result.ticker)
    tese = montar_tese_dividendo(
        ticker=result.ticker,
        preco=result.preco_atual,
        proventos=prov_tab,
        pct_bazin=pct_bazin,
        pct_graham=pct_graham,
        selic=selic,
        ipca_mais=ipca_mais,
        pl=fund.pl if fund else None,
        payout=fund.payout if fund else None,
        peso_setor_carteira=peso,
        setor=setor_chave,
        fund=fund,
        sentimento=sentimento,
        usar_sentimento=sent_ok,
    )

    # 1) Hero: tese | preço teto
    metricas_tese = [
        ("DY estimado", _fmt_pct(tese.dy_estimado)),
        ("Earnings yield", _fmt_pct(tese.earnings_yield)),
        ("vs Bazin", formatar_margem_pct(pct_bazin)),
        ("IPCA+ ref.", _fmt_pct(ipca_mais)),
    ]
    if tese.fcf_yield is not None:
        metricas_tese.append(("FCF yield", _fmt_pct(tese.fcf_yield)))
    elif tese.ev_ebit is not None:
        metricas_tese.append(("EV/EBIT", f"{tese.ev_ebit:.1f}x".replace(".", ",")))
    if tese.g_implicito is not None:
        metricas_tese.append(("g implícito", _fmt_pct(tese.g_implicito)))

    col_tese, col_teto = st.columns(2)
    with col_tese:
        card_decisao(
            tese.rotulo,
            tese.resumo,
            tom_de_decisao(tese.decisao),
            kicker="Tese",
            metricas=metricas_tese[:4],
        )
    with col_teto:
        card_decisao(
            result.veredito,
            (
                f"Anos: {', '.join(str(a) for a in result.anos_selecionados) or '—'} · "
                f"{result.metodo.value} · DY {_fmt_pct(result.dy_desejado)}"
            ),
            tom_de_diferenca(result.diferenca),
            kicker="Preço teto",
            metricas=[
                ("Base líquida", _fmt_money(result.base)),
                ("Preço teto", _fmt_money(result.preco_teto)),
                ("Preço atual", _fmt_money(result.preco_atual)),
                ("DY estimado", _fmt_pct(result.dy_atual)),
            ],
        )
    if len(metricas_tese) > 4:
        metricas_grid(metricas_tese[4:])
    card_listas(tese.a_favor, tese.contra)
    if tese.detalhe:
        st.caption(tese.detalhe)
    if sent_ok and sentimento:
        st.caption(f"Notícias diretas: {sentimento} · setor {setor_rot or '—'}")

    # 2) Valuation: Bazin | Graham
    _render_valuation_row(result, baz, gra)

    # 3) Proventos (histórico)
    _render_proventos_historico(result)

    # 4) Classes
    if result.ticker:
        _render_comparativo_classes(
            result.ticker,
            dy_desejado=result.dy_desejado,
            n_ultimos=n_ultimos,
            anos_especificos=anos_especificos,
            metodo=metodo,
            criterio=criterio,
            incluir_ano_andamento=incluir_ano_andamento,
            proventos_conhecidos=proventos_conhecidos or {},
        )


def _render_meses_pagamento(result: ValuationResult) -> None:
    if not result.meses_pagamento:
        return

    section_label("Meses mais frequentes de pagamento")
    st.caption("Contagem pela data de pagamento de cada provento desta ação.")

    top = result.meses_pagamento[:3]
    resumo = ", ".join(
        f"{m.nome} ({m.quantidade}x, {m.percentual * 100:.0f}%)" for m in top
    )
    st.write(f"Mais frequentes: **{resumo}**")

    col_t, col_c = st.columns([1, 1])
    with col_t:
        html_table(
            ["Mês", "Qtd", "%"],
            [
                [
                    m.nome,
                    m.quantidade,
                    f"{m.percentual * 100:.0f}".replace(".", ","),
                ]
                for m in result.meses_pagamento
            ],
            numeric_cols=[1, 2],
        )
    with col_c:
        chart_rows = sorted(result.meses_pagamento, key=lambda x: x.mes)
        chart_barras_horiz({m.nome: m.quantidade for m in chart_rows}, height=220)


def _render_top_nav() -> str:
    """Barra superior: páginas + tema + Ajustes (sem sidebar)."""
    col_pag, col_tema, col_aj = st.columns([4.0, 1.4, 1.1], vertical_alignment="bottom")
    with col_pag:
        st.caption("Página")
        pagina = st.radio(
            "Página",
            options=["Calculadora", "Por setor", "Carteira", "Assistente"],
            key="pagina_app",
            label_visibility="collapsed",
            horizontal=True,
        )
    with col_tema:
        st.caption("Tema")
        tema_atual = obter_tema()
        if "tema_ui_toggle" not in st.session_state:
            st.session_state.tema_ui_toggle = tema_atual
        escolha = st.radio(
            "Tema",
            options=["claro", "escuro"],
            format_func=lambda x: "Claro" if x == "claro" else "Escuro",
            key="tema_ui_toggle",
            label_visibility="collapsed",
            horizontal=True,
        )
        if escolha != tema_atual:
            definir_tema(escolha)
            st.rerun()
    with col_aj:
        st.caption("\u00a0")
        if hasattr(st, "popover"):
            with st.popover("Ajustes", use_container_width=True):
                _render_ajustes_globais(pagina)
        else:
            with st.expander("Ajustes", expanded=False):
                _render_ajustes_globais(pagina)
    return pagina


def _render_ajustes_globais(pagina: str) -> None:
    """Popover Ajustes: atalhos curtos (formulário longo fica em Configuração na página)."""
    inject_css_popover(obter_tema())
    if pagina == "Calculadora":
        if st.button("+ Nova aba vazia", use_container_width=True, key="top_nova_aba"):
            _commit_active_to_acoes()
            st.session_state.acoes.append(_default_acao())
            st.session_state.active_tab = len(st.session_state.acoes) - 1
            _hydrate_tab(st.session_state.active_tab)
            st.rerun()
        st.caption(
            "Parâmetros (Selic, IPCA+, anos) e salvas: **Configuração** no fim da página."
        )
    elif pagina == "Por setor":
        st.caption("Relatório de notícias na própria página Por setor.")
    elif pagina == "Assistente":
        st.caption("Chat com contexto da carteira e avaliações locais (Gemini).")
        if st.button("Limpar conversa", use_container_width=True, key="top_limpar_chat"):
            st.session_state.chat_msgs = []
            st.rerun()
    else:
        st.caption("Posições e CSV na página Carteira.")


def _sync_cfg_calculadora_from_widgets() -> dict:
    """Monta cfg a partir das chaves dos widgets (válido antes de renderizar Configuração)."""
    criterio_label = st.session_state.get("aj_criterio_ano", "Data-com")
    criterio = (
        YearCriterion.DATA_COM
        if criterio_label == "Data-com"
        else YearCriterion.DATA_PAGAMENTO
    )
    metodo_label = st.session_state.get("aj_metodo_base", "Média")
    metodo = {
        "Média": BaseMethod.MEDIA,
        "Mediana": BaseMethod.MEDIANA,
        "Menor ano (pessimista)": BaseMethod.MINIMO,
    }.get(metodo_label, BaseMethod.MEDIA)
    modo_anos = st.session_state.get("aj_modo_anos", "Últimos N anos fechados")
    n_ultimos: int | None = 5
    anos_especificos: list[int] | None = None
    if modo_anos == "Últimos N anos fechados":
        n_ultimos = int(st.session_state.get("aj_n_ultimos", 5) or 5)
    else:
        n_ultimos = None
        anos_str = str(st.session_state.get("aj_anos_str", "2023,2024,2025") or "")
        try:
            anos_especificos = [
                int(a.strip()) for a in anos_str.split(",") if a.strip()
            ]
        except ValueError:
            anos_especificos = []
    selic_pct = float(
        st.session_state.get("selic_ref_pct")
        or st.session_state.get("_selic_pct")
        or (SELIC_PADRAO * 100)
    )
    ipca_pct = float(
        st.session_state.get("ipca_mais_ref_pct")
        or st.session_state.get("_ipca_mais_pct")
        or (IPCA_MAIS_PADRAO * 100)
    )
    cfg = {
        "criterio": criterio,
        "metodo": metodo,
        "n_ultimos": n_ultimos,
        "anos_especificos": anos_especificos,
        "incluir_parcial": bool(st.session_state.get("aj_incluir_parcial", False)),
        "selic": selic_pct / 100.0,
        "ipca_mais": ipca_pct / 100.0,
    }
    st.session_state._calc_sidebar_cfg = cfg
    return cfg


def _render_configuracao_calculadora(salvas: list[AcaoSalva]) -> None:
    """Bloco único recolhido: análise + gestão de salvas (fim da Calculadora)."""
    with st.expander("Configuração", expanded=False):
        st.markdown("##### Análise")
        _render_ajustes_calculadora()
        st.divider()
        st.markdown("##### Ações salvas")
        _render_gestao_salvas_ajustes(salvas)


def _fmt_brl(v: float | None) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%".replace(".", ",")


def _sync_editor_posicoes(editor_key: str = "carteira_editor_rows") -> None:
    salvas = listar_posicoes()
    st.session_state[editor_key] = [
        {
            "ticker": p.ticker,
            "quantidade": p.quantidade,
            "preco_medio": p.preco_medio,
        }
        for p in salvas
    ] or [{"ticker": "", "quantidade": None, "preco_medio": None}]
    st.session_state.pop("carteira_data_editor", None)


def _push_undo_carteira() -> None:
    st.session_state.carteira_undo = push_undo_carteira(
        st.session_state.get("carteira_undo")
    )


def _render_pagina_carteira() -> None:
    """Posições reais: quantidade, preço médio, % do patrimônio."""
    page_header(
        "Carteira",
        "Quantidade, preço médio e peso no patrimônio. "
        "Compra recalcula o PM; use Desfazer se errar uma operação.",
    )
    st.session_state.setdefault("carteira_undo", [])

    editor_key = "carteira_editor_rows"
    if editor_key not in st.session_state:
        _sync_editor_posicoes(editor_key)

    n_undo = len(st.session_state.get("carteira_undo") or [])
    col_ops, col_undo = st.columns([3, 1])
    with col_undo:
        if st.button(
            "Desfazer",
            use_container_width=True,
            disabled=n_undo == 0,
            help="Volta a última compra, venda, importação ou ajuste manual.",
            key="btn_desfazer_carteira",
        ):
            hist, prev = desfazer_carteira(st.session_state.get("carteira_undo"))
            st.session_state.carteira_undo = hist
            _sync_editor_posicoes(editor_key)
            if prev is None:
                st.session_state.carteira_msg = "Nada para desfazer."
            else:
                st.session_state.carteira_msg = (
                    f"Desfeito. Carteira restaurada ({len(prev)} posição(ões))."
                )
            st.session_state.carteira_avisos = []
            st.rerun()
        if n_undo:
            st.caption(f"{n_undo} passo(s) na pilha")

    with col_ops:
        st.subheader("Registrar operação")
    tab_compra, tab_venda = st.tabs(["Compra", "Venda"])
    with tab_compra:
        with st.form("form_compra_carteira", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            ticker_c = c1.text_input("Ticker", placeholder="CMIG4")
            qtd_c = c2.number_input("Quantidade", min_value=0.0, value=0.0, step=1.0)
            preco_c = c3.number_input(
                "Preço unitário (R$)",
                min_value=0.0,
                value=0.0,
                step=0.01,
                format="%.4f",
                help="Preço por ação, não o valor total da ordem.",
            )
            st.caption("Informe o preço **por ação** (ex.: 14,18), não o total pago.")
            ok_c = st.form_submit_button("Adicionar compra", type="primary")
        if ok_c:
            try:
                with st.spinner("Salvando compra…"):
                    _push_undo_carteira()
                    pos = aplicar_compra(
                        ticker=ticker_c, quantidade=float(qtd_c), preco=float(preco_c)
                    )
                    avisos = garantir_acoes_das_posicoes([pos])
                _sync_editor_posicoes(editor_key)
                st.session_state.carteira_msg = (
                    f"{pos.ticker}: {pos.quantidade:g} un. · PM R$ {pos.preco_medio:.4f}"
                    .replace(".", ",")
                )
                st.session_state.carteira_avisos = avisos
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
    with tab_venda:
        with st.form("form_venda_carteira", clear_on_submit=True):
            c1, c2 = st.columns(2)
            ticker_v = c1.text_input("Ticker", placeholder="CMIG4", key="venda_ticker")
            qtd_v = c2.number_input(
                "Quantidade", min_value=0.0, value=0.0, step=1.0, key="venda_qtd"
            )
            st.caption("Na venda o preço médio da posição restante não muda.")
            ok_v = st.form_submit_button("Registrar venda")
        if ok_v:
            try:
                _push_undo_carteira()
                pos = aplicar_venda(ticker=ticker_v, quantidade=float(qtd_v))
                _sync_editor_posicoes(editor_key)
                if pos is None:
                    st.session_state.carteira_msg = (
                        f"{str(ticker_v).strip().upper()}: posição zerada e removida."
                    )
                else:
                    st.session_state.carteira_msg = (
                        f"{pos.ticker}: restam {pos.quantidade:g} un. · "
                        f"PM R$ {pos.preco_medio:.4f}".replace(".", ",")
                    )
                st.session_state.carteira_avisos = []
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    msg = st.session_state.pop("carteira_msg", None)
    if msg:
        st.success(msg)
    for av in st.session_state.pop("carteira_avisos", None) or []:
        st.caption(av)

    salvas_pos = listar_posicoes()
    with st.expander("Ajuste manual / CSV", expanded=False):
        st.caption(
            "Use só para correção pontual ou carga inicial. "
            "No dia a dia, prefira Compra/Venda acima. Desfazer também cobre estes ajustes."
        )
        up = st.file_uploader("CSV da carteira", type=["csv", "txt"], key="carteira_csv_up")
        if up is not None and st.button("Importar CSV (substitui posições)", key="btn_imp_cart"):
            try:
                texto = up.getvalue().decode("utf-8-sig")
                parsed = parse_csv_carteira(texto)
                if not parsed:
                    st.warning("CSV sem linhas válidas.")
                else:
                    with st.spinner("Salvando posições e cadastrando tickers novos…"):
                        _push_undo_carteira()
                        substituir_posicoes(
                            [(p.ticker, p.quantidade, p.preco_medio) for p in parsed]
                        )
                        avisos = garantir_acoes_das_posicoes(parsed)
                    _sync_editor_posicoes(editor_key)
                    st.session_state.carteira_msg = (
                        f"Importadas {len(parsed)} posição(ões)."
                    )
                    st.session_state.carteira_avisos = avisos
                    st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

        csv_atual = export_csv_carteira(salvas_pos)
        st.download_button(
            "Baixar CSV atual",
            data=csv_atual.encode("utf-8"),
            file_name="carteira.csv",
            mime="text/csv",
            disabled=not salvas_pos,
            key="btn_dl_cart",
        )

        edited = st.data_editor(
            pd.DataFrame(st.session_state[editor_key]),
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "ticker": st.column_config.TextColumn("Ticker", required=False, width="small"),
                "quantidade": st.column_config.NumberColumn(
                    "Quantidade", min_value=0.0, format="%.4f", step=1.0
                ),
                "preco_medio": st.column_config.NumberColumn(
                    "Preço médio", min_value=0.0, format="%.4f", step=0.01
                ),
            },
            key="carteira_data_editor",
        )
        col_s, col_r = st.columns(2)
        with col_s:
            if st.button("Salvar ajuste manual", use_container_width=True):
                try:
                    rows_ed = (
                        edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)
                    )
                    parsed = posicoes_de_editor(rows_ed)
                    with st.spinner("Salvando…"):
                        _push_undo_carteira()
                        substituir_posicoes(
                            [(p.ticker, p.quantidade, p.preco_medio) for p in parsed]
                        )
                        avisos = garantir_acoes_das_posicoes(parsed)
                    _sync_editor_posicoes(editor_key)
                    st.session_state.carteira_msg = f"Salvas {len(parsed)} posição(ões)."
                    st.session_state.carteira_avisos = avisos
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
        with col_r:
            if st.button("Recarregar do banco", use_container_width=True):
                _sync_editor_posicoes(editor_key)
                st.rerun()

    posicoes = listar_posicoes()
    if not posicoes:
        st.info("Nenhuma posição ainda. Registre uma compra ou importe um CSV.")
        return

    section_label("Visão da carteira")
    if st.button(
        "Atualizar proventos da carteira",
        help="Busca de novo proventos (e preço) de todos os tickers e grava no banco.",
        key="btn_atualizar_proventos_carteira",
        type="primary",
    ):
        with st.spinner(
            f"Atualizando proventos de {len(posicoes)} ticker(s)… pode levar um tempo."
        ):
            avisos = atualizar_proventos_carteira(posicoes)
        st.session_state.carteira_msg = (
            f"Proventos atualizados: {len(posicoes)} ticker(s)."
        )
        st.session_state.carteira_avisos = avisos
        st.rerun()

    empresas = load_empresas()
    precos: dict[str, float | None] = {}
    with st.spinner("Atualizando preços…"):
        for p in posicoes:
            preco: float | None = None
            try:
                cot = fetch_preco(p.ticker)
                if cot and cot.preco and cot.preco > 0:
                    preco = float(cot.preco)
            except Exception:  # noqa: BLE001
                salva = obter_acao(p.ticker)
                if salva and salva.preco > 0:
                    preco = float(salva.preco)
            precos[p.ticker] = preco

    setores = {p.ticker: setor_do_ticker(p.ticker, empresas) for p in posicoes}
    enriq = enriquecer_posicoes(posicoes, precos, setores=setores)
    datas_com: dict[str, object] = {}
    for p in posicoes:
        salva = obter_acao(p.ticker)
        datas_com[p.ticker] = resumo_datas_com(salva.proventos if salva else "")
    total = sum(e.valor_mercado or 0.0 for e in enriq)
    custo_total = sum(e.custo for e in enriq)

    # Agrega peso por setor para a pizza
    peso_setor: dict[str, float] = {}
    for e in enriq:
        if e.valor_mercado and e.valor_mercado > 0:
            rot = rotulo_setor(e.setor)
            peso_setor[rot] = peso_setor.get(rot, 0.0) + float(e.valor_mercado)

    col_pat, col_pie = st.columns([1, 1])
    with col_pat:
        st.metric("Patrimônio (posições)", _fmt_brl(total if total > 0 else None))
        st.caption(f"Custo total (qty × PM): {_fmt_brl(custo_total)}")
        if peso_setor and total > 0:
            linhas_setor = sorted(
                peso_setor.items(), key=lambda kv: -kv[1]
            )
            html_table(
                ["Setor", "Valor", "%"],
                [
                    [
                        s,
                        _fmt_brl(v),
                        f"{(v / total) * 100:.1f}%".replace(".", ","),
                    ]
                    for s, v in linhas_setor
                ],
                numeric_cols=[1, 2],
            )
    with col_pie:
        section_label("Por setor")
        chart_pizza_setores(list(peso_setor.items()), height=280)

    section_label("Análise da carteira")
    st.caption(
        "Evolução aproximada: quantidade atual × preços históricos. "
        "Não reflete compras/vendas feitas no meio do período."
    )
    opcoes_periodo = list(RANGES_HISTORICO.keys())
    periodo_cart = st.selectbox(
        "Período",
        options=opcoes_periodo,
        index=opcoes_periodo.index("1y") if "1y" in opcoes_periodo else 0,
        format_func=lambda k: RANGES_HISTORICO[k],
        key="carteira_hist_periodo",
    )
    with st.spinner("Montando evolução do património…"):
        serie = serie_patrimonio_aprox(posicoes, periodo=periodo_cart)
    if serie.avisos:
        with st.expander("Avisos do histórico", expanded=False):
            for a in serie.avisos[:12]:
                st.caption(a)
    if len(serie.datas) >= 2:
        m1, m2, m3 = st.columns(3)
        m1.metric("Var. no período", _fmt_pct(serie.var_periodo))
        m2.metric("Patrimônio (fim)", _fmt_brl(serie.patrimonio[-1]))
        m3.metric("Vs custo (PM)", _fmt_pct(serie.resultado_vs_custo))
        df_nav = pd.DataFrame(
            {
                "Patrimônio": serie.patrimonio,
                "Custo (qty × PM)": serie.custo,
            },
            index=pd.to_datetime(serie.datas),
        )
        chart_linhas(df_nav, y_cols=["Patrimônio", "Custo (qty × PM)"], height=300)
        if serie.contribuicoes:
            section_label("Quem mais moveu no período")
            st.caption("Variação do valor de mercado de cada papel (qty atual × Δ preço).")
            contrib_ord = dict(
                sorted(serie.contribuicoes.items(), key=lambda kv: -abs(kv[1]))[:10]
            )
            chart_barras_horiz(
                {t: round(v, 2) for t, v in contrib_ord.items()},
                height=max(180, 28 * len(contrib_ord)),
            )
    else:
        st.caption("Não foi possível montar a série de património neste período.")

    rows_cart: list[list[object]] = []
    for e in sorted(enriq, key=lambda x: -(x.peso_pct or 0)):
        dc = datas_com.get(e.ticker)
        rows_cart.append(
            [
                e.ticker,
                rotulo_setor(e.setor),
                e.quantidade,
                f"{e.preco_medio:.2f}".replace(".", ",") if e.preco_medio else "—",
                (
                    f"{e.preco_atual:.2f}".replace(".", ",")
                    if e.preco_atual is not None
                    else "—"
                ),
                (
                    f"{e.valor_mercado:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    if e.valor_mercado is not None
                    else "—"
                ),
                (
                    f"{e.peso_pct * 100:.1f}%".replace(".", ",")
                    if e.peso_pct is not None
                    else "—"
                ),
                _fmt_pct(e.resultado_pct),
                dc.fmt_proxima() if dc else "—",
                dc.fmt_ultima() if dc else "—",
            ]
        )
    html_table(
        [
            "Ticker",
            "Setor",
            "Qtd",
            "PM",
            "Preço",
            "Valor",
            "%",
            "Resultado",
            "Próx. com",
            "Últ. com",
        ],
        rows_cart,
        numeric_cols=[2, 3, 4, 5, 6, 7],
        link_cols={0: "abrir"},
    )
    st.caption(
        "Clique no ticker para abrir na Calculadora. "
        "Data-com vem dos proventos salvos (próxima futura ou “—” se não houver). "
        "Use **Atualizar proventos da carteira** quando sair anúncio novo."
    )

    st.subheader("Estimativa de proventos e DY")
    st.caption(
        "Não é previsão oficial: média líquida dos últimos anos fechados "
        "(data-com, com IR de JCP). **DY estimado** = R$/ação da média ÷ preço atual. "
        "Faixa = pior/melhor ano do histórico ÷ preço. Empresas mudam o payout."
    )
    n_anos_est = st.selectbox(
        "Anos fechados na média",
        options=[3, 5, 2],
        index=0,
        key="carteira_est_anos",
        help="Mesma ideia da base da calculadora (anos fechados, sem o ano corrente).",
    )
    est = estimar_proventos_carteira(posicoes, n_anos=int(n_anos_est))
    dy_cart = (est.total_medio / total) if total > 0 and est.total_medio > 0 else None
    metricas_grid(
        [
            ("Estimativa (média)", _fmt_brl(est.total_medio)),
            ("~ / mês", _fmt_brl(est.mensal_medio)),
            ("Mín. histórico", _fmt_brl(est.total_min)),
            ("Máx. histórico", _fmt_brl(est.total_max)),
            ("DY carteira (est.)", _fmt_pct(dy_cart)),
        ]
    )
    if dy_cart is not None:
        st.caption(
            f"Se repetir o último ano fechado: {_fmt_brl(est.total_ultimo)} "
            f"(~{_fmt_pct(est.total_ultimo / total if total > 0 else None)} a.a.)."
        )

    rows_est: list[list[object]] = []
    for e in sorted(
        est.por_acao,
        key=lambda x: -(x.total_medio or 0),
    ):
        px = precos.get(e.ticker)
        dy_m = dy_estimado(e.por_acao_medio, px)
        dy_lo = dy_estimado(e.por_acao_min, px)
        dy_hi = dy_estimado(e.por_acao_max, px)
        rows_est.append(
            [
                e.ticker,
                e.quantidade,
                f"{px:.2f}".replace(".", ",") if px is not None else "—",
                (
                    f"{e.por_acao_medio:.4f}".replace(".", ",")
                    if e.por_acao_medio is not None
                    else "—"
                ),
                f"{dy_m * 100:.1f}".replace(".", ",") if dy_m is not None else "—",
                (
                    f"{dy_lo * 100:.1f}–{dy_hi * 100:.1f}".replace(".", ",")
                    if dy_lo is not None and dy_hi is not None
                    else "—"
                ),
                _fmt_brl(e.total_medio),
                ", ".join(str(a) for a in e.anos_usados) or "—",
                e.aviso or "",
            ]
        )
    html_table(
        [
            "Ticker",
            "Qtd",
            "Preço",
            "R$/ação",
            "DY %",
            "DY faixa",
            "R$ posição",
            "Anos",
            "Obs.",
        ],
        rows_est,
        numeric_cols=[1, 2, 3, 4, 6],
    )

    st.subheader("Meses de proventos")
    st.caption(
        "● data de pagamento já anunciada (futura) · "
        "○ mês mais provável pelo histórico (apareceu em ≥2 anos). "
        "Não é garantia de pagamento."
    )
    cal = calendario_proventos_carteira(posicoes)
    mes_headers = [rotulo_mes_curto(m) for m in range(1, 13)]
    rows_cal: list[list[object]] = []
    for c in cal.por_acao:
        row: list[object] = [c.ticker]
        for m in range(1, 13):
            row.append(c.marca_mes(m))
        row.append(c.aviso or "")
        rows_cal.append(row)
    html_table(["Ticker", *mes_headers, "Obs."], rows_cal)

    oficiais = [
        (c.ticker, o)
        for c in cal.por_acao
        for o in c.oficiais
    ]
    if oficiais:
        st.markdown("**Pagamentos anunciados**")
        html_table(
            ["Ticker", "Pagamento", "R$/ação", "Tipo"],
            [
                [
                    t,
                    o.data.strftime("%d/%m/%Y"),
                    f"{o.valor_bruto:.4f}".replace(".", ","),
                    o.tipo,
                ]
                for t, o in sorted(oficiais, key=lambda x: x[1].data)
            ],
            numeric_cols=[2],
        )

    if cal.por_mes:
        st.markdown("**Por mês (oficial + estimado)**")
        html_table(
            ["Mês", "Tickers"],
            [[rotulo_mes_curto(m), ", ".join(ticks)] for m, ticks in cal.por_mes],
        )

def _sidebar_catalogo_empresas() -> None:
    """Lista tickers do empresas.toml (por setor) para abrir na Calculadora."""
    empresas = _empresas_mapa()
    if not empresas:
        return
    por_setor: dict[str, list] = {}
    for emp in empresas.values():
        por_setor.setdefault(emp.setor or "outros", []).append(emp)
    with st.expander("Catálogo (abrir ticker)", expanded=False):
        st.caption(
            "Empresas do `empresas.toml` — inclusive saneamento. "
            "Escolha e abra; preço/proventos buscam sozinhos."
        )
        setores = sorted(por_setor.keys(), key=lambda s: rotulo_setor(s).lower())
        # Prefere saneamento se existir (pedido recente do usuário)
        idx = setores.index("saneamento") if "saneamento" in setores else 0
        setor = st.selectbox(
            "Setor",
            options=setores,
            index=idx,
            format_func=rotulo_setor,
            key="catalogo_setor",
        )
        opcoes = sorted(por_setor.get(setor, []), key=lambda e: e.ticker_preferencial)
        if not opcoes:
            st.caption("Nenhuma empresa neste setor.")
            return
        escolha = st.selectbox(
            "Empresa",
            options=opcoes,
            format_func=lambda e: f"{e.ticker_preferencial} · {e.nome_principal}",
            key="catalogo_empresa",
        )
        if st.button("Abrir na Calculadora", use_container_width=True, key="btn_catalogo_abrir"):
            _abrir_acao_salva_ou_nova(escolha.ticker_preferencial)
            st.rerun()


def _abrir_acao_salva_ou_nova(ticker: str) -> None:
    """Abre salva se existir; senão preenche a aba ativa com o ticker novo."""
    t = str(ticker or "").strip().upper()
    if not t:
        return
    if obter_acao(t) is not None:
        _abrir_acao_salva(t)
        return
    i = int(st.session_state.get("active_tab", 0) or 0)
    acoes = st.session_state.setdefault("acoes", [_default_acao()])
    if i < 0 or i >= len(acoes):
        i = 0
        st.session_state.active_tab = 0
    acoes[i] = {"ticker": t, "preco": 0.0, "dy": 6.0, "proventos": ""}
    st.session_state[f"ticker_{i}"] = t
    st.session_state[f"preco_{i}"] = 0.0
    st.session_state[f"dy_{i}"] = 6.0
    st.session_state[f"proventos_{i}"] = ""
    st.session_state.pop(f"_auto_ticker_{i}", None)
    _on_ticker_change(i)


def _render_busca_abrir(salvas: list[AcaoSalva]) -> None:
    """Busca compacta para abrir ticker (gestão fica em Ajustes)."""
    _render_lista_salvas(salvas)


def _render_ajustes_calculadora() -> dict:
    """Controles da Calculadora (popover Ajustes); grava config em session_state."""
    st.markdown("**Base de proventos**")
    criterio_label = st.radio(
        "Critério do ano",
        options=["Data-com", "Data de pagamento"],
        index=0,
        help="Mesmo critério para todas as ações.",
        key="aj_criterio_ano",
    )
    criterio = (
        YearCriterion.DATA_COM
        if criterio_label == "Data-com"
        else YearCriterion.DATA_PAGAMENTO
    )

    metodo_label = st.selectbox(
        "Método da base",
        options=["Média", "Mediana", "Menor ano (pessimista)"],
        index=0,
        key="aj_metodo_base",
    )
    metodo = {
        "Média": BaseMethod.MEDIA,
        "Mediana": BaseMethod.MEDIANA,
        "Menor ano (pessimista)": BaseMethod.MINIMO,
    }[metodo_label]

    modo_anos = st.radio(
        "Anos considerados",
        options=["Últimos N anos fechados", "Anos específicos"],
        index=0,
        key="aj_modo_anos",
    )

    n_ultimos: int | None = 5
    anos_especificos: list[int] | None = None

    if modo_anos == "Últimos N anos fechados":
        n_ultimos = int(
            st.number_input(
                "N anos fechados",
                min_value=1,
                max_value=30,
                value=5,
                step=1,
                key="aj_n_ultimos",
            )
        )
    else:
        n_ultimos = None
        anos_str = st.text_input(
            "Anos (separados por vírgula)",
            value="2023,2024,2025",
            help="Ex.: 2021,2023,2024 para pular um ano atípico.",
            key="aj_anos_str",
        )
        try:
            anos_especificos = [
                int(a.strip()) for a in anos_str.split(",") if a.strip()
            ]
        except ValueError:
            st.error("Informe anos válidos, ex.: 2022,2023,2024")
            anos_especificos = []

    incluir_parcial = st.checkbox(
        "Incluir ano em andamento",
        value=False,
        help="Se ligado, o ano corrente entra pelo valor parcial (com aviso).",
        key="aj_incluir_parcial",
    )

    st.markdown("**Tese**")
    selic_pct = st.number_input(
        "Selic de referência (% a.a.)",
        min_value=1.0,
        max_value=30.0,
        value=float(st.session_state.get("_selic_pct", SELIC_PADRAO * 100)),
        step=0.25,
        help="Custo de oportunidade nominal (DY / earnings yield vs Selic).",
        key="selic_ref_pct",
    )
    st.session_state._selic_pct = selic_pct
    ipca_pct = st.number_input(
        "IPCA+ de referência (% a.a.)",
        min_value=0.5,
        max_value=15.0,
        value=float(st.session_state.get("_ipca_mais_pct", IPCA_MAIS_PADRAO * 100)),
        step=0.10,
        help="Yield real do Tesouro IPCA+ (ajuste pelo número do dia no Tesouro Direto).",
        key="ipca_mais_ref_pct",
    )
    st.session_state._ipca_mais_pct = ipca_pct

    st.markdown("**Preço ao vivo**")
    st.caption("Preços das abas abertas atualizam automaticamente.")
    st.selectbox(
        "Intervalo",
        options=[15, 30, 60, 120],
        format_func=lambda s: f"{s} segundos",
        key="auto_preco_intervalo",
    )
    status = st.session_state.get("auto_preco_status")
    st.caption(status or "Aguardando primeira atualização…")
    if st.button("Atualizar todas agora", use_container_width=True, key="aj_btn_preco"):
        st.session_state._last_auto_fetch = 0
        n_ok, erros = _atualizar_precos_abertas()
        for j, acao in enumerate(st.session_state.acoes):
            _queue_update(f"preco_{j}", float(acao.get("preco") or 0.0))
        horario = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%H:%M:%S")
        msg = f"Auto-preço: {n_ok} atualizada(s) às {horario}"
        if erros:
            msg += f" | falhas: {'; '.join(erros[:3])}"
        st.session_state.auto_preco_status = msg
        st.rerun()

    if st.button("+ Nova aba vazia", use_container_width=True, key="aj_nova_aba"):
        _commit_active_to_acoes()
        st.session_state.acoes.append(_default_acao())
        st.session_state.active_tab = len(st.session_state.acoes) - 1
        _hydrate_tab(st.session_state.active_tab)
        st.rerun()

    cfg = {
        "criterio": criterio,
        "metodo": metodo,
        "n_ultimos": n_ultimos,
        "anos_especificos": anos_especificos,
        "incluir_parcial": incluir_parcial,
        "selic": float(selic_pct) / 100.0,
        "ipca_mais": float(ipca_pct) / 100.0,
    }
    st.session_state._calc_sidebar_cfg = cfg
    return cfg


def _render_bloco_relatorio_noticias() -> None:
    section_label("Relatório de notícias")
    st.caption("Gera e envia o e-mail com análise por setor.")
    usar_ia = st.checkbox(
        "Usar IA (Gemini)",
        value=True,
        key="relatorio_usar_ia",
        help="Desligue para análise automática simples, sem chamar a IA.",
    )
    apply_runtime_secrets(getattr(st, "secrets", None))
    smtp_ok = smtp_config_from_env() is not None
    if not smtp_ok:
        st.warning(
            "E-mail ainda não configurado. Crie `.streamlit/secrets.toml` "
            "com SMTP_HOST, SMTP_USER, SMTP_PASSWORD e ALERT_EMAIL_TO."
        )
    if st.button(
        "Gerar e enviar por e-mail",
        use_container_width=True,
        disabled=not smtp_ok,
        key="btn_relatorio_noticias",
    ):
        with st.spinner(
            "Buscando notícias e montando o relatório… isso pode levar 1–3 minutos."
        ):
            try:
                apply_runtime_secrets(getattr(st, "secrets", None))
                rel = gerar_relatorio(usar_ia=bool(usar_ia))
                enviar_relatorio(rel)
                st.session_state.relatorio_msg = (
                    f"Enviado ({rel.motor}): {len(rel.metricas)} ações, "
                    f"{len(rel.noticias)} notícias relevantes."
                )
                st.session_state.relatorio_erros = list(rel.avisos[:8])
            except Exception as exc:  # noqa: BLE001
                st.session_state.relatorio_msg = ""
                st.session_state.relatorio_erros = [str(exc)]
        st.rerun()
    msg_rel = st.session_state.get("relatorio_msg") or ""
    erros_rel = st.session_state.get("relatorio_erros") or []
    if msg_rel:
        st.success(msg_rel)
    for err in erros_rel:
        st.caption(f"Aviso: {err}")


def _pack_noticia_chat(n, *, tipo: str) -> dict[str, str]:
    """Campos ricos de uma notícia para o prompt do Assistente."""
    pub = ""
    if getattr(n, "publicado", None) is not None:
        try:
            pub = n.publicado.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            pub = str(n.publicado)[:10]
    return {
        "tipo": tipo,
        "titulo": (n.titulo or "")[:180],
        "resumo": (n.resumo or "")[:280],
        "impacto": _impacto_por_palavras(n),
        "fonte": n.fonte or "",
        "temas": ", ".join(n.temas or [])[:80],
        "publicado": pub,
    }


def _noticias_contexto_chat(
    tickers: list[str],
) -> tuple[
    dict[str, list[dict[str, str]]],
    list[dict[str, str]],
    dict[str, dict],
]:
    """
    Pacote de notícias para o chat.

    Devolve:
    - diretas por ticker (até 5)
    - notícias de setor/tema ligadas aos tickers (até 12)
    - sentimento por ticker {sentimento, confiante, n_diretas}
    """
    tickers_u = [t.upper() for t in tickers]
    diretas: dict[str, list[dict[str, str]]] = {t: [] for t in tickers_u}
    setor: list[dict[str, str]] = []
    sentimento: dict[str, dict] = {}
    if not tickers_u:
        return diretas, setor, sentimento

    noticias = list(_noticias_cacheadas())
    if not noticias:
        return diretas, setor, sentimento

    empresas = _empresas_mapa()
    ligar_noticias(noticias, tickers_u, empresas)
    vistos_setor: set[str] = set()

    for n in noticias:
        pack_dir = None
        for t in n.acoes_diretas:
            chave = t.upper()
            if chave not in diretas:
                continue
            if len(diretas[chave]) >= 5:
                continue
            if pack_dir is None:
                pack_dir = _pack_noticia_chat(n, tipo="direta")
            diretas[chave].append(dict(pack_dir))

        liga_setor = any(t.upper() in diretas for t in (n.acoes_tema or []))
        if liga_setor and n.id not in vistos_setor and len(setor) < 12:
            # evita duplicar se já entrou como direta de algum ticker pedido
            if not any(t.upper() in diretas for t in n.acoes_diretas):
                vistos_setor.add(n.id)
                row = _pack_noticia_chat(n, tipo="setor")
                row["tickers_tema"] = ", ".join(
                    t.upper() for t in n.acoes_tema if t.upper() in diretas
                )[:60]
                setor.append(row)

    for t in tickers_u:
        sent, conf = sentimento_rapido_ticker(t, noticias)
        sentimento[t] = {
            "sentimento": sent,
            "confiante": conf,
            "n_diretas": len(diretas.get(t) or []),
        }
    return diretas, setor, sentimento


def _mercado_contexto_chat(tickers: list[str], *, limite: int = 4) -> dict[str, dict]:
    """Variação dia/mês (Yahoo) para poucos tickers prioritários do chat."""
    out: dict[str, dict] = {}
    for t in tickers[:limite]:
        chave = t.upper()
        try:
            hist = fetch_historico(chave, periodo="1mo", timeout=10.0)
            fechamentos = [p.preco for p in hist.pontos if p.preco]
            if not fechamentos:
                continue
            var_dia = None
            var_mes = None
            if len(fechamentos) >= 2 and fechamentos[-2]:
                var_dia = fechamentos[-1] / fechamentos[-2] - 1.0
            if fechamentos[0]:
                var_mes = fechamentos[-1] / fechamentos[0] - 1.0
            ref = var_mes if var_mes is not None else var_dia
            if ref is None:
                tendencia = "sem dados"
            elif ref > 0.02:
                tendencia = "alta"
            elif ref < -0.02:
                tendencia = "queda"
            else:
                tendencia = "lateral"
            out[chave] = {
                "preco_ultimo": round(fechamentos[-1], 4),
                "var_dia_pct": None if var_dia is None else round(var_dia * 100, 2),
                "var_mes_pct": None if var_mes is None else round(var_mes * 100, 2),
                "tendencia": tendencia,
            }
        except Exception:  # noqa: BLE001
            continue
    return out


def _foco_ticker_calculadora() -> str | None:
    i = int(st.session_state.get("active_tab", 0) or 0)
    t = str(st.session_state.get(f"ticker_{i}", "") or "").strip().upper()
    if not t or t == "EXEMPLO":
        return None
    return t.removesuffix(".SA")


def _tickers_abas_abertas() -> list[str]:
    """Tickers das abas da Calculadora (session), mesmo antes de gravar no banco."""
    out: list[str] = []
    for i, acao in enumerate(st.session_state.get("acoes") or []):
        t = str(
            st.session_state.get(f"ticker_{i}", acao.get("ticker", "")) or ""
        ).strip().upper().removesuffix(".SA")
        if t and t != "EXEMPLO" and t not in out:
            out.append(t)
    return out


def _acoes_abertas_para_chat() -> list[AcaoSalva]:
    """Espelha abas abertas como AcaoSalva para o contexto do Assistente."""
    agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")
    out: list[AcaoSalva] = []
    for i, acao in enumerate(st.session_state.get("acoes") or []):
        t = str(
            st.session_state.get(f"ticker_{i}", acao.get("ticker", "")) or ""
        ).strip().upper().removesuffix(".SA")
        if not t or t == "EXEMPLO":
            continue
        preco = float(
            st.session_state.get(f"preco_{i}", acao.get("preco", 0.0)) or 0.0
        )
        dy = float(st.session_state.get(f"dy_{i}", acao.get("dy", 6.0)) or 6.0)
        proventos = str(
            st.session_state.get(f"proventos_{i}", acao.get("proventos", "")) or ""
        )
        out.append(
            AcaoSalva(
                ticker=t,
                preco=preco,
                dy=dy,
                proventos=proventos,
                updated_at=agora,
            )
        )
    return out


def _mesclar_acoes_chat(
    salvas: list[AcaoSalva], abertas: list[AcaoSalva]
) -> list[AcaoSalva]:
    """Abas abertas sobrescrevem o banco (dados mais frescos da Calculadora)."""
    por: dict[str, AcaoSalva] = {a.ticker.upper(): a for a in salvas}
    for a in abertas:
        t = a.ticker.upper()
        prev = por.get(t)
        # preferir aba se tiver preço; senão manter salva
        if prev is None or (a.preco and a.preco > 0):
            if prev and not (a.proventos or "").strip() and (prev.proventos or "").strip():
                por[t] = AcaoSalva(
                    ticker=t,
                    preco=a.preco if a.preco > 0 else prev.preco,
                    dy=a.dy or prev.dy,
                    proventos=prev.proventos,
                    updated_at=a.updated_at,
                )
            else:
                por[t] = a
    return list(por.values())


def _garantir_ticker_para_chat(ticker: str) -> AcaoSalva | None:
    """
    Se o ticker foi citado mas ainda não está no banco/aba com dados,
    busca preço + proventos na hora (e tenta auto-salvar).
    """
    t = (ticker or "").strip().upper().removesuffix(".SA")
    if not t or t == "EXEMPLO":
        return None
    existente = obter_acao(t)
    if existente and existente.preco > 0 and (existente.proventos or "").strip():
        return existente
    preco = float(existente.preco) if existente and existente.preco > 0 else 0.0
    dy = float(existente.dy) if existente else 6.0
    proventos = existente.proventos if existente else ""
    try:
        if preco <= 0:
            cot = fetch_preco(t)
            preco = float(cot.preco)
    except Exception:  # noqa: BLE001
        pass
    try:
        if not (proventos or "").strip():
            got = fetch_proventos(t)
            proventos = got.texto or ""
    except Exception:  # noqa: BLE001
        pass
    if preco <= 0:
        return existente
    agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")
    if (proventos or "").strip():
        try:
            return salvar_acao(ticker=t, preco=preco, dy=dy or 6.0, proventos=proventos)
        except Exception:  # noqa: BLE001
            pass
    return AcaoSalva(
        ticker=t, preco=preco, dy=dy or 6.0, proventos=proventos or "", updated_at=agora
    )


def _render_pagina_assistente() -> None:
    """Chat Gemini com contexto da carteira / avaliações locais."""
    apply_runtime_secrets(getattr(st, "secrets", None))
    page_header(
        "Assistente",
        "Pergunte sobre a carteira, comparar ações ou a leitura Comprar/Esperar/Evitar. "
        "Usa catálogo salvo + abas abertas na Calculadora + Gemini — indicativo, não recomendação.",
    )
    st.session_state.setdefault("chat_msgs", [])

    api_ok = bool(gemini_api_key())
    if not api_ok:
        st.warning(
            "Configure `GEMINI_API_KEY` em `.streamlit/secrets.toml` para usar o chat. "
            "Sem a chave, o Assistente fica desligado."
        )

    cols = st.columns(len(SUGESTOES_PADRAO))
    for i, sug in enumerate(SUGESTOES_PADRAO):
        if cols[i].button(sug, use_container_width=True, key=f"chat_sug_{i}", disabled=not api_ok):
            st.session_state._chat_pending = sug
            st.rerun()

    for m in st.session_state.chat_msgs:
        with st.chat_message(m["papel"]):
            st.markdown(m["texto"])

    pending = st.session_state.pop("_chat_pending", None)
    # st.chat_input usa Emotion com secondaryBg do theme.base=light do
    # config.toml — texto some no Escuro. Form + text_input herda nosso CSS.
    with st.form("assistente_chat_form", clear_on_submit=True, border=False):
        col_msg, col_btn = st.columns([7, 1], vertical_alignment="bottom")
        with col_msg:
            prompt = st.text_input(
                "Mensagem",
                placeholder="Ex.: Compare ITUB4 e BBAS3; como está minha carteira?",
                label_visibility="collapsed",
                disabled=not api_ok,
                key="assistente_chat_texto",
            )
        with col_btn:
            enviado = st.form_submit_button(
                "Enviar",
                type="primary",
                use_container_width=True,
                disabled=not api_ok,
            )
    pergunta = (pending or (prompt if enviado else "") or "").strip()
    if not pergunta or not api_ok:
        return

    st.session_state.chat_msgs.append({"papel": "user", "texto": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)

    cfg = st.session_state.get("_calc_sidebar_cfg") or {}
    selic = float(cfg.get("selic", SELIC_PADRAO))
    ipca = float(cfg.get("ipca_mais", IPCA_MAIS_PADRAO))
    fund_cache: dict = st.session_state.get("fund_cache") or {}
    posicoes = listar_posicoes()
    salvas_db = listar_acoes()
    abertas = _acoes_abertas_para_chat()
    salvas = _mesclar_acoes_chat(salvas_db, abertas)
    candidatos_locais = list(
        dict.fromkeys(
            _tickers_abas_abertas()
            + [a.ticker.upper() for a in salvas_db]
            + [p.ticker.upper() for p in posicoes]
        )
    )
    citados = extrair_tickers_texto(pergunta, candidatos=candidatos_locais)
    # Citou ticker ainda sem dados → busca na hora e grava no catálogo
    for t in citados[:4]:
        if any(a.ticker.upper() == t and a.preco > 0 for a in salvas):
            continue
        novo = _garantir_ticker_para_chat(t)
        if novo:
            salvas = _mesclar_acoes_chat(salvas, [novo])

    ideias = pergunta_ideias_aporte(pergunta)
    foco = _foco_ticker_calculadora()
    if ideias:
        tickers_ctx = list(
            dict.fromkeys(
                citados
                + [a.ticker.upper() for a in salvas]
                + [p.ticker.upper() for p in posicoes]
                + ([foco] if foco else [])
            )
        )
        lim_ctx = 20
    else:
        tickers_ctx = list(
            dict.fromkeys(
                citados
                + ([foco] if foco else [])
                + _tickers_abas_abertas()
                + [p.ticker.upper() for p in posicoes]
                + [a.ticker.upper() for a in salvas]
            )
        )
        lim_ctx = 12
    faltam = [t for t in tickers_ctx[:lim_ctx] if t not in fund_cache]
    if faltam:
        _preencher_fund_cache(faltam)
        fund_cache = st.session_state.get("fund_cache") or {}

    precos: dict[str, float | None] = {}
    for a in salvas:
        if a.preco and a.preco > 0:
            precos[a.ticker.upper()] = float(a.preco)
    empresas = _empresas_mapa()
    setores = {t: setor_do_ticker(t, empresas) for t in tickers_ctx}
    noticias, noticias_setor, sentimento = _noticias_contexto_chat(tickers_ctx[:lim_ctx])
    prioridade_mercado = list(
        dict.fromkeys(
            citados + ([foco] if foco else []) + tickers_ctx[:6]
        )
    )
    mercado = _mercado_contexto_chat(prioridade_mercado, limite=6 if ideias else 4)

    hist = [
        MensagemChat(papel=m["papel"], texto=m["texto"])
        for m in st.session_state.chat_msgs[:-1]
        if m.get("papel") in ("user", "assistant")
        # não reenviar falhas técnicas ao Gemini (poluem a próxima resposta)
        and not str(m.get("texto") or "").startswith("Não consegui responder agora:")
    ]
    with st.chat_message("assistant"):
        with st.spinner("Consultando Gemini com seus dados locais…"):
            try:
                ctx = montar_contexto_chat(
                    pergunta=pergunta,
                    selic=selic,
                    ipca_mais=ipca,
                    foco_ticker=foco,
                    posicoes=posicoes,
                    acoes=salvas,
                    fund_cache=fund_cache,
                    precos=precos,
                    setores=setores,
                    noticias_por_ticker=noticias,
                    noticias_setor=noticias_setor,
                    sentimento_por_ticker=sentimento,
                    mercado_por_ticker=mercado,
                )
                texto, modelo = responder_chat(
                    pergunta, contexto=ctx, historico=hist
                )
                st.markdown(texto)
                st.caption(f"Modelo: {modelo} · indicativo — não é recomendação de investimento.")
                st.session_state.chat_msgs.append({"papel": "assistant", "texto": texto})
            except Exception as exc:  # noqa: BLE001
                err = f"Não consegui responder agora: {exc}"
                st.error(err)
                st.session_state.chat_msgs.append({"papel": "assistant", "texto": err})


def main() -> None:
    _init_state()
    _consumir_query_abrir()
    _aplicar_navegacao_pendente()
    _apply_pending_updates()
    inject_css(obter_tema())

    salvas = listar_acoes()
    pagina = _render_top_nav()

    if pagina == "Por setor":
        _render_pagina_setores(salvas)
        _render_bloco_relatorio_noticias()
        return

    if pagina == "Carteira":
        _render_pagina_carteira()
        return

    if pagina == "Assistente":
        _render_pagina_assistente()
        return

    page_header(
        "Calculadora",
        "Escolha um papel na busca; preço e proventos vêm sozinhos.",
    )

    cfg = _sync_cfg_calculadora_from_widgets()
    criterio = cfg["criterio"]
    metodo = cfg["metodo"]
    n_ultimos = cfg["n_ultimos"]
    anos_especificos = cfg["anos_especificos"]
    incluir_parcial = cfg["incluir_parcial"]

    # Garante chaves da aba ativa a partir de acoes se ainda não existirem
    for i, acao in enumerate(st.session_state.acoes):
        st.session_state.setdefault(f"ticker_{i}", acao.get("ticker", ""))
        st.session_state.setdefault(f"preco_{i}", float(acao.get("preco") or 0.0))
        st.session_state.setdefault(f"dy_{i}", float(acao.get("dy") or 6.0))
        st.session_state.setdefault(f"proventos_{i}", acao.get("proventos", ""))

    i = int(st.session_state.active_tab)
    if i < 0 or i >= len(st.session_state.acoes):
        i = 0
        st.session_state.active_tab = 0

    tem_ticker = any(
        str(st.session_state.get(f"ticker_{j}", a.get("ticker", "")) or "").strip()
        for j, a in enumerate(st.session_state.acoes)
    )

    if not tem_ticker:
        # Um único começo: busca (salvas + catálogo). Digitação livre só no atalho.
        section_label("Abrir análise")
        _render_busca_abrir(salvas)
        with st.expander("Ticker fora da lista", expanded=False):
            st.caption("Código B3 que não aparece na busca — Enter carrega preço e proventos.")
            st.text_input(
                "Ticker novo",
                key=f"ticker_{i}",
                placeholder="Ex.: ITUB4",
                on_change=_on_ticker_change,
                args=(i,),
            )
        st.session_state.acoes = _snapshot_acoes_from_widgets()
        _render_configuracao_calculadora(salvas)
        _rodar_auto_precos()
        return

    # Com ticker: troca rápida → abas → papel → contexto → resultado → proventos → config
    with st.expander("Trocar ou abrir outro papel", expanded=False):
        _render_busca_abrir(salvas)

    _render_tab_bar()

    col_a, col_b, col_c = st.columns(3)
    col_a.text_input(
        "Papel ativo",
        key=f"ticker_{i}",
        placeholder="Ex.: ITUB4",
        on_change=_on_ticker_change,
        args=(i,),
    )
    col_b.number_input(
        "Preço atual (R$)",
        min_value=0.0,
        step=0.01,
        format="%.4f",
        key=f"preco_{i}",
    )
    col_c.number_input(
        "DY desejado (%)",
        min_value=0.01,
        step=0.1,
        format="%.2f",
        key=f"dy_{i}",
    )

    ticker_atual = str(st.session_state.get(f"ticker_{i}", "") or "").strip()
    empresa = None
    if ticker_atual:
        chave = ticker_atual.upper().removesuffix(".SA")
        cache = st.session_state.setdefault("empresa_cache", {})
        if chave not in cache:
            with st.spinner("Buscando descrição da empresa…"):
                empresa = _obter_empresa(ticker_atual)
        else:
            empresa = _obter_empresa(ticker_atual)

    status_bits = []
    for key in (f"save_msg_{i}", f"preco_msg_{i}", f"proventos_msg_{i}"):
        msg = st.session_state.pop(key, None)
        if msg:
            status_bits.append(msg)
    if status_bits:
        st.caption(" · ".join(status_bits))

    for aviso in st.session_state.pop(f"proventos_avisos_{i}", []) or []:
        st.warning(aviso)

    if ticker_atual:
        prov_txt = str(st.session_state.get(f"proventos_{i}", "") or "")
        section_label("Contexto")
        c_emp, c_graf = st.columns(2)
        with c_emp:
            _render_empresa(empresa, ticker=ticker_atual, proventos=prov_txt)
        with c_graf:
            _render_grafico_preco(ticker_atual, i)

    # Sincroniza antes do resultado (widgets ticker/preço/DY já renderizados)
    st.session_state.acoes = _snapshot_acoes_from_widgets()

    acao_ativa = st.session_state.acoes[i]
    _tentar_auto_salvar(
        i,
        ticker=str(acao_ativa.get("ticker", "") or ""),
        preco=float(acao_ativa.get("preco") or 0),
        dy=float(acao_ativa.get("dy") or 6),
        proventos=str(acao_ativa.get("proventos") or ""),
    )

    resultados: list[ValuationResult] = []
    for j, acao in enumerate(st.session_state.acoes):
        ticker = str(acao.get("ticker", "")).strip()
        preco = float(acao.get("preco") or 0)
        dy = float(acao.get("dy") or 6)
        proventos = str(acao.get("proventos") or "")

        if not (proventos.strip() and preco > 0):
            continue

        result = calculate(
            proventos,
            preco_atual=preco,
            dy_desejado=dy / 100.0,
            ticker=ticker or f"Ação {j + 1}",
            n_ultimos=n_ultimos,
            anos_especificos=anos_especificos,
            metodo=metodo,
            criterio=criterio,
            incluir_ano_andamento=incluir_parcial,
            ano_corrente=date.today().year,
        )
        resultados.append(result)
        if j == i:
            conhecidos = {
                str(a.get("ticker", "")).strip().upper(): str(a.get("proventos") or "")
                for a in st.session_state.acoes
                if str(a.get("ticker", "")).strip() and str(a.get("proventos") or "").strip()
            }
            _render_resultado(
                result,
                n_ultimos=n_ultimos,
                anos_especificos=anos_especificos,
                metodo=metodo,
                criterio=criterio,
                incluir_ano_andamento=incluir_parcial,
                proventos_conhecidos=conhecidos,
            )

    if len(resultados) >= 2:
        st.divider()
        section_label("Comparativo")
        empresas = _empresas_mapa()

        def _linha_comparativo(r: ValuationResult) -> list[object]:
            def _n(v: float | None, casas: int = 2) -> str:
                if v is None:
                    return "—"
                return f"{v:.{casas}f}".replace(".", ",")

            setor = rotulo_setor(setor_do_ticker(r.ticker, empresas))
            teto_baz = pct_baz = "—"
            justo_gra = pct_gra = "—"
            if r.base is not None and r.base > 0 and r.preco_atual > 0:
                baz = conta_bazin(r.base, r.preco_atual)
                teto_baz = _n(baz.preco_justo, 4)
                pct_baz = _n(baz.diferenca * 100 if baz.diferenca is not None else None)
            fund = _obter_fundamentos(r.ticker)
            if (
                fund
                and fund.lpa is not None
                and fund.vpa is not None
                and fund.lpa > 0
                and fund.vpa > 0
                and r.preco_atual > 0
            ):
                try:
                    gra = conta_graham(fund.lpa, fund.vpa, r.preco_atual)
                    justo_gra = _n(gra.preco_justo, 4)
                    pct_gra = _n(
                        gra.diferenca * 100 if gra.diferenca is not None else None
                    )
                except Exception:  # noqa: BLE001
                    pass
            return [
                setor,
                r.ticker,
                _n(r.base, 4),
                _n(r.dy_atual * 100 if r.dy_atual is not None else None),
                _n(r.preco_teto, 4),
                _n(r.preco_atual, 4),
                _n(r.diferenca * 100 if r.diferenca is not None else None),
                r.veredito,
                teto_baz,
                pct_baz,
                justo_gra,
                pct_gra,
            ]

        for setor, grupo in agrupar_por_setor(
            resultados,
            setor_de=lambda r: setor_do_ticker(r.ticker, empresas),
            chave_item=lambda r: (
                r.diferenca is None,
                r.diferenca if r.diferenca is not None else 0,
                r.ticker,
            ),
        ):
            st.markdown(f"**{rotulo_setor(setor)}** ({len(grupo)})")
            html_table(
                [
                    "Setor",
                    "Ticker",
                    "Base",
                    "DY %",
                    "Teto",
                    "Preço",
                    "% vs teto",
                    "Veredito",
                    "Teto Bazin",
                    "% Bazin",
                    "Graham",
                    "% Graham",
                ],
                [_linha_comparativo(r) for r in grupo],
                numeric_cols=[2, 3, 4, 5, 6, 8, 9, 10, 11],
            )
        st.caption(
            "% vs teto negativo = barata (abaixo do teto). Dentro de cada setor, "
            "ordenado da mais barata para a mais cara."
        )

    with st.expander("Proventos e opções", expanded=False):
        st.text_area(
            "Proventos (edite manualmente se quiser)",
            height=220,
            key=f"proventos_{i}",
            placeholder=EXEMPLO.splitlines()[0],
        )

        if st.button("Recarregar preço e proventos", key=f"reload_{i}"):
            ticker = str(st.session_state.get(f"ticker_{i}", "") or "").strip()
            if ticker:
                try:
                    cot = fetch_preco(ticker)
                    _queue_update(f"preco_{i}", float(cot.preco))
                    quando = (
                        cot.horario.strftime("%d/%m/%Y %H:%M")
                        if cot.horario
                        else "agora"
                    )
                    st.session_state[f"preco_msg_{i}"] = (
                        f"Preço {cot.ticker}: R$ {cot.preco:.4f} "
                        f"({cot.fonte}, {quando} BRT)"
                    )
                except Exception as exc:  # noqa: BLE001
                    st.session_state[f"preco_msg_{i}"] = f"Preço: {exc}"
                try:
                    got = fetch_proventos(ticker)
                    _queue_update(f"proventos_{i}", got.texto)
                    st.session_state[f"proventos_msg_{i}"] = (
                        f"{got.quantidade} provento(s) via {got.fonte}"
                    )
                    if got.avisos:
                        st.session_state[f"proventos_avisos_{i}"] = got.avisos[:8]
                except Exception as exc:  # noqa: BLE001
                    st.session_state[f"proventos_msg_{i}"] = f"Proventos: {exc}"
                st.session_state[f"_auto_ticker_{i}"] = ticker.upper()
                st.rerun()
        if st.button("Colar exemplo", key=f"exemplo_{i}"):
            _queue_update(f"proventos_{i}", EXEMPLO)
            if not str(st.session_state.get(f"ticker_{i}", "") or "").strip():
                _queue_update(f"ticker_{i}", "EXEMPLO")
            if float(st.session_state.get(f"preco_{i}", 0) or 0) <= 0:
                _queue_update(f"preco_{i}", 10.0)
            st.rerun()

    # Re-sincroniza após edição de proventos no expander
    st.session_state.acoes = _snapshot_acoes_from_widgets()

    _render_configuracao_calculadora(salvas)
    _rodar_auto_precos()


if __name__ == "__main__":
    main()
