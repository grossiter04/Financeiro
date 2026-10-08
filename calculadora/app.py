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
    calendario_proventos_carteira,
    enriquecer_posicoes,
    estimar_proventos_carteira,
    export_csv_carteira,
    garantir_acoes_das_posicoes,
    parse_csv_carteira,
    posicoes_de_editor,
    rotulo_mes_curto,
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
from calculadora.mail import apply_runtime_secrets, smtp_config_from_env
from calculadora.noticias import load_empresas
from calculadora.relatorio import enviar_relatorio, gerar_relatorio
from calculadora.setores import agrupar_por_setor, rotulo_setor, setor_do_ticker
from calculadora.sinal import ItemFila, calcular_sinal, formatar_margem_pct, item_fila_de_salva
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

st.set_page_config(page_title="Preço teto por proventos", layout="wide")

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
    return info


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
    """Selectbox por setor + lista agrupada na sidebar."""
    empresas = _empresas_mapa()
    grupos = agrupar_por_setor(
        salvas,
        setor_de=lambda a: setor_do_ticker(a.ticker, empresas),
        chave_item=lambda a: a.ticker,
    )
    # Opções com setor no rótulo; valor = ticker
    opcoes: list[str] = []
    rotulos: dict[str, str] = {}
    for setor, grupo in grupos:
        for a in grupo:
            opcoes.append(a.ticker)
            rotulos[a.ticker] = f"{rotulo_setor(setor)} · {a.ticker}"

    def _abrir_salva_callback() -> None:
        ticker = st.session_state.get("sidebar_abrir_ticker")
        if not ticker:
            return
        if st.session_state.get("pagina_app") == "Por setor":
            _ir_para_calculadora(str(ticker))
        else:
            _abrir_acao_salva(str(ticker))

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
            "Abrir",
            options=opcoes,
            index=None,
            format_func=lambda t: rotulos.get(t, t),
            placeholder="Escolha uma ação…",
            key="sidebar_abrir_ticker",
            on_change=_abrir_salva_callback,
            help="Ações agrupadas por setor.",
        )
    st.caption(f"{len(salvas)} salva(s) · {len(grupos)} setor(es)")
    with st.expander("Por setor", expanded=False):
        for setor, grupo in grupos:
            tickers = ", ".join(a.ticker for a in grupo)
            st.markdown(f"**{rotulo_setor(setor)}** ({len(grupo)})  \n{tickers}")


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
    st.line_chart(df, height=240)
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


def _inject_tab_css() -> None:
    """Remove o padding extra do iframe das abas e estiliza a fila do topo."""
    st.markdown(
        """
        <style>
        iframe[title="calculadora.tab_bar.browser_tabs"] {
          margin-bottom: -0.6rem;
        }
        div[data-testid="stAppViewContainer"] > .main .block-container {
          padding-top: 1.1rem;
        }
        .fila-acoes {
          overflow: hidden;
          border: 1px solid rgba(148, 163, 184, 0.22);
          border-radius: 8px;
          background: rgba(30, 41, 59, 0.35);
          margin: 0 0 0.65rem 0;
          mask-image: linear-gradient(90deg, transparent, #000 4%, #000 96%, transparent);
          -webkit-mask-image: linear-gradient(90deg, transparent, #000 4%, #000 96%, transparent);
        }
        .fila-track {
          display: flex;
          width: max-content;
          gap: 0.45rem;
          padding: 0.45rem 0.7rem;
          animation: fila-scroll 70s linear infinite;
        }
        .fila-acoes:hover .fila-track { animation-play-state: paused; }
        .fila-chip {
          display: inline-flex;
          align-items: center;
          gap: 0.3rem;
          white-space: nowrap;
          padding: 0.22rem 0.55rem;
          border-radius: 6px;
          font-size: 0.78rem;
          font-weight: 450;
          color: #cbd5e1;
          border: 1px solid rgba(148, 163, 184, 0.18);
          background: rgba(15, 23, 42, 0.4);
        }
        .fila-dot {
          width: 0.4rem;
          height: 0.4rem;
          border-radius: 50%;
          flex-shrink: 0;
          opacity: 0.85;
        }
        .fila-setor {
          display: inline-flex;
          align-items: center;
          white-space: nowrap;
          padding: 0.22rem 0.5rem;
          border-radius: 5px;
          font-size: 0.72rem;
          font-weight: 600;
          color: #94a3b8;
          background: transparent;
          border: 1px solid rgba(148, 163, 184, 0.2);
        }
        @keyframes fila-scroll {
          from { transform: translateX(0); }
          to { transform: translateX(-50%); }
        }
        @media (prefers-reduced-motion: reduce) {
          .fila-track { animation: none; flex-wrap: wrap; width: 100%; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _empresas_mapa() -> dict:
    cache = st.session_state.get("_empresas_mapa")
    if cache is not None:
        return cache
    mapa = load_empresas()
    st.session_state._empresas_mapa = mapa
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
        itens.append(
            item_fila_de_salva(
                ticker=a.ticker,
                preco=a.preco,
                dy=a.dy,
                proventos=a.proventos,
                lpa=lpa,
                vpa=vpa,
                setor=setor_do_ticker(a.ticker, empresas),
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
    markup = (
        f'<div class="fila-acoes" aria-label="Fila de status das ações salvas por setor">'
        f'<div class="fila-track">{faixa}{faixa}</div></div>'
    )
    st.markdown(markup, unsafe_allow_html=True)
    st.caption(
        "Fila por setor (Bazin/Graham) · atrativa / neutra / esticada. "
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
    st.session_state._pending_abrir_ticker = t


def _aplicar_navegacao_pendente() -> None:
    """Aplica troca de página / abertura de ticker antes de criar widgets."""
    pending = st.session_state.pop("_pending_pagina", None)
    if pending in ("Calculadora", "Por setor", "Carteira"):
        st.session_state.pagina_app = pending
    ticker = st.session_state.pop("_pending_abrir_ticker", None)
    if ticker:
        _abrir_acao_salva(str(ticker))


def _render_pagina_setores(salvas: list[AcaoSalva]) -> None:
    """Página dedicada: visão da carteira por setor."""
    st.title("Visão por setor")
    st.caption(
        "Leitura suave vs teto Bazin/Graham nas ações salvas. "
        "Margens absurdas (sem DY útil) aparecem como “muito acima/abaixo” "
        "e não forçam o sinal. Use a Calculadora para detalhar uma ação."
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
            "atrativa": sum(1 for i in grupo if i.sinal.codigo == "compra"),
            "neutra": sum(1 for i in grupo if i.sinal.codigo == "aguardar"),
            "esticada": sum(1 for i in grupo if i.sinal.codigo == "cautela"),
        }
        bits = [f"{k} {v}" for k, v in contagem.items() if v]
        st.subheader(
            f"{rotulo_setor(setor)} · {len(grupo)}"
            + (f" · {', '.join(bits)}" if bits else "")
        )
        rows = [
            {
                "Ticker": it.ticker,
                "Preço": (
                    f"{it.preco:.2f}".replace(".", ",") if it.preco and it.preco > 0 else "—"
                ),
                "Próx. data-com": it.datas_com.fmt_proxima(),
                "Últ. data-com": it.datas_com.fmt_ultima(),
                "Bazin": formatar_margem_pct(it.pct_bazin),
                "Graham": formatar_margem_pct(it.pct_graham),
                "Leitura": it.sinal.rotulo,
            }
            for it in grupo
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption(
        "Data-com vem dos proventos salvos (próxima futura ou última já passada). "
        "Graham usa LPA/VPA — “—” se a fonte não devolveu dados positivos."
    )


def _render_sinal_orientativo(
    *,
    pct_bazin: float | None,
    pct_graham: float | None,
    sentimento: str | None = None,
) -> None:
    sinal = calcular_sinal(
        pct_bazin=pct_bazin, pct_graham=pct_graham, sentimento=sentimento
    )
    st.subheader("Leitura vs Bazin/Graham")
    if sinal.codigo == "compra":
        st.success(f"**{sinal.rotulo}**")
    elif sinal.codigo == "cautela":
        st.warning(f"**{sinal.rotulo}**")
    else:
        st.info(f"**{sinal.rotulo}**")
    st.caption(sinal.resumo)
    st.caption(sinal.detalhe)


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
    dif = conta.diferenca or 0.0
    if dif < 0:
        st.success(f"**{conta.nome}** — {conta.veredito}")
    elif dif > 0:
        st.error(f"**{conta.nome}** — {conta.veredito}")
    else:
        st.info(f"**{conta.nome}** — {conta.veredito}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Preço justo", _fmt_money(conta.preco_justo))
    m2.metric("Preço atual", _fmt_money(conta.preco_atual))
    m3.metric("% vs justo", _fmt_pct(conta.diferenca))
    if conta.detalhe:
        st.caption(conta.detalhe)


def _render_contas_classicas(result: ValuationResult) -> None:
    """Bazin (proventos ÷ 6%) e Graham (√22,5×LPA×VPA)."""
    st.subheader("Contas clássicas")
    col_b, col_g = st.columns(2)
    pct_bazin: float | None = None
    pct_graham: float | None = None

    with col_b:
        if result.base is not None and result.base > 0 and result.preco_atual > 0:
            baz = conta_bazin(result.base, result.preco_atual)
            pct_bazin = baz.diferenca
            _render_conta_card(baz)
            if abs(result.dy_desejado - BAZIN_DY) > 1e-9:
                st.caption(
                    f"Seu DY desejado na calculadora é {_fmt_pct(result.dy_desejado)}; "
                    f"Bazin usa fixo {_fmt_pct(BAZIN_DY)}."
                )
        else:
            st.caption("Bazin: precisa de base de proventos e preço.")

    with col_g:
        fund = _obter_fundamentos(result.ticker)
        if fund is None:
            st.caption("Graham: não encontrei LPA/VPA para este ticker.")
        elif fund.lpa is None or fund.vpa is None:
            st.caption(
                f"Graham: dados incompletos "
                f"(LPA={fund.lpa}, VPA={fund.vpa}, fonte {fund.fonte})."
            )
        elif fund.lpa <= 0 or fund.vpa <= 0:
            st.caption(
                f"Graham: LPA/VPA precisam ser positivos "
                f"(LPA={fund.lpa:.4f}, VPA={fund.vpa:.4f})."
            )
        else:
            try:
                gra = conta_graham(fund.lpa, fund.vpa, result.preco_atual)
                pct_graham = gra.diferenca
                _render_conta_card(gra)
                st.caption(f"LPA {fund.lpa:.4f} · VPA {fund.vpa:.4f} · {fund.fonte}")
            except Exception as exc:  # noqa: BLE001
                st.caption(f"Graham: {exc}")

    _render_sinal_orientativo(pct_bazin=pct_bazin, pct_graham=pct_graham)


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

    det = [
        {
            "Componente": tick,
            "Qtd": qtd,
            "Preço": round(preco, 4),
            "Subtotal": round(sub, 4),
        }
        for tick, qtd, preco, sub in analise.componentes
    ]
    st.dataframe(det, use_container_width=True, hide_index=True)


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

    st.subheader("Qual classe comprar? (3 / 4 / 11)")
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

    tabela = []
    for c in linhas:
        marca = " ← aberta" if c.ticker == atual else ""
        tabela.append(
            {
                "Ticker": f"{c.ticker}{marca}",
                "Tipo": c.tipo,
                "Preço": round(c.preco, 4),
                "DY atual %": round(c.dy_atual * 100, 2) if c.dy_atual is not None else None,
                "% seu DY": round(c.pct_usuario * 100, 2) if c.pct_usuario is not None else None,
                "Teto seu DY": round(c.teto_usuario, 4) if c.teto_usuario is not None else None,
                "% Bazin": round(c.pct_bazin * 100, 2) if c.pct_bazin is not None else None,
                "Teto Bazin": round(c.teto_bazin, 4) if c.teto_bazin is not None else None,
                "% Graham": round(c.pct_graham * 100, 2) if c.pct_graham is not None else None,
                "Teto Graham": round(c.teto_graham, 4) if c.teto_graham is not None else None,
            }
        )
    st.dataframe(tabela, use_container_width=True, hide_index=True)

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
    if result.erros:
        for err in result.erros:
            st.warning(err)

    if result.aviso:
        st.info(result.aviso)

    if result.veredito in ("Erro", "Sem dados"):
        st.error(result.veredito + (f" — {result.aviso}" if result.aviso else ""))
        if result.meses_pagamento:
            _render_meses_pagamento(result)
        return

    diferenca = result.diferenca or 0.0
    if diferenca < 0:
        st.success(f"**{result.veredito}**")
    elif diferenca > 0:
        st.error(f"**{result.veredito}**")
    else:
        st.info(f"**{result.veredito}**")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Base líquida", _fmt_money(result.base))
    c2.metric("Preço teto", _fmt_money(result.preco_teto))
    c3.metric("Preço atual", _fmt_money(result.preco_atual))
    c4.metric("DY atual", _fmt_pct(result.dy_atual))

    st.caption(
        f"Anos na base: {', '.join(str(a) for a in result.anos_selecionados) or '—'} "
        f"| Método: {result.metodo.value} | DY desejado: {_fmt_pct(result.dy_desejado)}"
    )

    if result.anos_resumo:
        selecionados = set(result.anos_selecionados)
        rows = []
        for r in reversed(result.anos_resumo):
            marca = ""
            if r.parcial:
                marca = " (parcial)"
            elif r.ano in selecionados:
                marca = " ✓"
            rows.append(
                {
                    "Ano": f"{r.ano}{marca}",
                    "Bruto": round(r.bruto, 8),
                    "IR (JCP)": round(r.ir, 8),
                    "Líquido": round(r.liquido, 8),
                    "Na base": r.ano in selecionados,
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    _render_meses_pagamento(result)
    _render_contas_classicas(result)
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

    st.subheader("Meses mais frequentes de pagamento")
    st.caption("Contagem pela data de pagamento de cada provento desta ação.")

    top = result.meses_pagamento[:3]
    resumo = ", ".join(
        f"{m.nome} ({m.quantidade}x, {m.percentual * 100:.0f}%)" for m in top
    )
    st.write(f"Mais frequentes: **{resumo}**")

    rows = [
        {
            "Mês": m.nome,
            "Quantidade": m.quantidade,
            "% do total": round(m.percentual * 100, 1),
        }
        for m in result.meses_pagamento
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    chart_rows = sorted(result.meses_pagamento, key=lambda x: x.mes)
    st.bar_chart(
        {m.nome: m.quantidade for m in chart_rows},
        horizontal=True,
        sort=False,
    )


def _sidebar_navegacao() -> str:
    st.header("Navegação")
    return st.radio(
        "Página",
        options=["Calculadora", "Por setor", "Carteira"],
        key="pagina_app",
        label_visibility="collapsed",
        horizontal=True,
    )


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


def _render_pagina_carteira() -> None:
    """Posições reais: quantidade, preço médio, % do patrimônio."""
    st.title("Carteira")
    st.caption(
        "Registre compras no formulário — o preço médio é recalculado sozinho. "
        "No Inter: Invest → lupa → “Informe de posições” (e-mail) se quiser "
        "conferir o extrato; CSV ainda serve para carga inicial."
    )

    editor_key = "carteira_editor_rows"
    if editor_key not in st.session_state:
        _sync_editor_posicoes(editor_key)

    st.subheader("Registrar operação")
    tab_compra, tab_venda = st.tabs(["Compra", "Venda"])
    with tab_compra:
        with st.form("form_compra_carteira", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            ticker_c = c1.text_input("Ticker", placeholder="CMIG4")
            qtd_c = c2.number_input("Quantidade", min_value=0.0, value=0.0, step=1.0)
            preco_c = c3.number_input(
                "Preço da compra (R$)", min_value=0.0, value=0.0, step=0.01, format="%.4f"
            )
            ok_c = st.form_submit_button("Adicionar compra", type="primary")
        if ok_c:
            try:
                with st.spinner("Salvando compra…"):
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
            "No dia a dia, prefira Compra/Venda acima."
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

    st.subheader("Visão da carteira")
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
    total = sum(e.valor_mercado or 0.0 for e in enriq)
    custo_total = sum(e.custo for e in enriq)
    st.metric("Patrimônio (posições)", _fmt_brl(total if total > 0 else None))
    st.caption(f"Custo total (qty × PM): {_fmt_brl(custo_total)}")

    rows = [
        {
            "Ticker": e.ticker,
            "Setor": rotulo_setor(e.setor),
            "Qtd": e.quantidade,
            "PM": e.preco_medio,
            "Preço": e.preco_atual if e.preco_atual is not None else "—",
            "Valor": e.valor_mercado if e.valor_mercado is not None else "—",
            "% carteira": (
                f"{e.peso_pct * 100:.1f}%".replace(".", ",")
                if e.peso_pct is not None
                else "—"
            ),
            "Resultado": _fmt_pct(e.resultado_pct),
        }
        for e in sorted(enriq, key=lambda x: -(x.peso_pct or 0))
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader("Estimativa de proventos")
    st.caption(
        "Não é previsão: usa a média líquida dos últimos anos fechados "
        "(data-com, com IR de JCP) × quantidade. A faixa é o pior/melhor "
        "ano do histórico usado. Empresas cortam ou aumentam dividendos."
    )
    n_anos_est = st.selectbox(
        "Anos fechados na média",
        options=[3, 5, 2],
        index=0,
        key="carteira_est_anos",
        help="Mesma ideia da base da calculadora (anos fechados, sem o ano corrente).",
    )
    est = estimar_proventos_carteira(posicoes, n_anos=int(n_anos_est))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Estimativa (média)", _fmt_brl(est.total_medio))
    c2.metric("~ / mês", _fmt_brl(est.mensal_medio))
    c3.metric("Faixa (min–máx)", f"{_fmt_brl(est.total_min)} – {_fmt_brl(est.total_max)}")
    c4.metric(
        f"Se repetir {max((e.ano_ultimo for e in est.por_acao if e.ano_ultimo), default='—')}",
        _fmt_brl(est.total_ultimo),
    )
    if total > 0 and est.total_medio > 0:
        st.caption(
            f"Yield estimado sobre o patrimônio atual: "
            f"{(est.total_medio / total) * 100:.1f}%".replace(".", ",")
            + " a.a. (média ÷ valor de mercado)."
        )

    rows_est = []
    for e in sorted(
        est.por_acao,
        key=lambda x: -(x.total_medio or 0),
    ):
        rows_est.append(
            {
                "Ticker": e.ticker,
                "Qtd": e.quantidade,
                "R$/ação (média)": (
                    f"{e.por_acao_medio:.4f}".replace(".", ",")
                    if e.por_acao_medio is not None
                    else "—"
                ),
                "Estimativa": _fmt_brl(e.total_medio),
                "Faixa": (
                    f"{_fmt_brl(e.total_min)} – {_fmt_brl(e.total_max)}"
                    if e.total_medio is not None
                    else "—"
                ),
                "Anos": ", ".join(str(a) for a in e.anos_usados) or "—",
                "Obs.": e.aviso or "",
            }
        )
    st.dataframe(rows_est, use_container_width=True, hide_index=True)

    st.subheader("Meses de proventos")
    st.caption(
        "● data de pagamento já anunciada (futura) · "
        "○ mês mais provável pelo histórico (apareceu em ≥2 anos). "
        "Não é garantia de pagamento."
    )
    cal = calendario_proventos_carteira(posicoes)
    rows_cal = []
    for c in cal.por_acao:
        row = {"Ticker": c.ticker}
        for m in range(1, 13):
            row[rotulo_mes_curto(m)] = c.marca_mes(m)
        row["Obs."] = c.aviso or ""
        rows_cal.append(row)
    st.dataframe(rows_cal, use_container_width=True, hide_index=True)

    oficiais = [
        (c.ticker, o)
        for c in cal.por_acao
        for o in c.oficiais
    ]
    if oficiais:
        st.markdown("**Pagamentos anunciados**")
        st.dataframe(
            [
                {
                    "Ticker": t,
                    "Pagamento": o.data.strftime("%d/%m/%Y"),
                    "R$/ação": f"{o.valor_bruto:.4f}".replace(".", ","),
                    "Tipo": o.tipo,
                }
                for t, o in sorted(oficiais, key=lambda x: x[1].data)
            ],
            use_container_width=True,
            hide_index=True,
        )

    if cal.por_mes:
        st.markdown("**Por mês (oficial + estimado)**")
        st.dataframe(
            [
                {
                    "Mês": rotulo_mes_curto(m),
                    "Tickers": ", ".join(ticks),
                }
                for m, ticks in cal.por_mes
            ],
            use_container_width=True,
            hide_index=True,
        )

    tickers = [e.ticker for e in enriq]
    escolha = st.selectbox(
        "Abrir na Calculadora",
        options=["—"] + tickers,
        key="carteira_abrir_ticker",
    )
    if escolha and escolha != "—" and st.button("Abrir ticker", key="btn_cart_abrir"):
        _ir_para_calculadora(escolha)
        st.rerun()


def _sidebar_calculadora_controles(salvas: list[AcaoSalva]) -> dict:
    """Controles da Calculadora na sidebar; grava config em session_state."""
    st.header("Ações salvas")
    if salvas:
        _render_lista_salvas(salvas)
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
        st.caption("Nenhuma ação salva ainda. Digite um ticker na aba.")

    st.divider()
    st.header("Controles gerais")

    criterio_label = st.radio(
        "Critério do ano",
        options=["Data-com", "Data de pagamento"],
        index=0,
        help="Mesmo critério para todas as ações.",
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
    )

    n_ultimos: int | None = 5
    anos_especificos: list[int] | None = None

    if modo_anos == "Últimos N anos fechados":
        n_ultimos = st.number_input(
            "N anos fechados",
            min_value=1,
            max_value=30,
            value=5,
            step=1,
        )
    else:
        n_ultimos = None
        anos_str = st.text_input(
            "Anos (separados por vírgula)",
            value="2023,2024,2025",
            help="Ex.: 2021,2023,2024 para pular um ano atípico.",
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
    )

    st.divider()
    st.subheader("Preço ao vivo")
    st.caption("Preços das abas abertas atualizam automaticamente.")
    st.selectbox(
        "Intervalo",
        options=[15, 30, 60, 120],
        format_func=lambda s: f"{s} segundos",
        key="auto_preco_intervalo",
    )
    status = st.session_state.get("auto_preco_status")
    if status:
        st.caption(status)
    else:
        st.caption("Aguardando primeira atualização…")
    if st.button("Atualizar todas agora", use_container_width=True):
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

    st.divider()
    if st.button("+ Nova aba vazia"):
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
    }
    st.session_state._calc_sidebar_cfg = cfg
    return cfg


def main() -> None:
    _init_state()
    _aplicar_navegacao_pendente()
    _apply_pending_updates()
    _inject_tab_css()

    salvas = listar_acoes()

    with st.sidebar:
        pagina = _sidebar_navegacao()
        st.divider()

        if pagina == "Por setor":
            st.caption(
                f"{len(salvas)} ação(ões) salva(s). "
                "Detalhe de cada ticker na Calculadora."
            )
            if salvas:
                _render_lista_salvas(salvas)
            st.divider()
            st.subheader("Relatório de notícias")
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
        elif pagina == "Carteira":
            n_pos = len(listar_posicoes())
            st.caption(
                f"{n_pos} posição(ões) · edite na página ou importe CSV. "
                "Tickers novos entram também na Calculadora."
            )
        else:
            cfg = _sidebar_calculadora_controles(salvas)

    if pagina == "Por setor":
        _render_pagina_setores(salvas)
        return

    if pagina == "Carteira":
        _render_pagina_carteira()
        return

    criterio = cfg["criterio"]
    metodo = cfg["metodo"]
    n_ultimos = cfg["n_ultimos"]
    anos_especificos = cfg["anos_especificos"]
    incluir_parcial = cfg["incluir_parcial"]

    st.title("Calculadora de preço teto por proventos")
    st.caption(
        "Digite o ticker: preço, proventos e descrição vêm sozinhos. "
        "A ação é salva automaticamente. Posições (qty/PM) na página **Carteira**; "
        "visão por setor em **Por setor**."
    )

    # Garante chaves da aba ativa a partir de acoes se ainda não existirem
    for i, acao in enumerate(st.session_state.acoes):
        st.session_state.setdefault(f"ticker_{i}", acao.get("ticker", ""))
        st.session_state.setdefault(f"preco_{i}", float(acao.get("preco") or 0.0))
        st.session_state.setdefault(f"dy_{i}", float(acao.get("dy") or 6.0))
        st.session_state.setdefault(f"proventos_{i}", acao.get("proventos", ""))

    _render_tab_bar()

    i = int(st.session_state.active_tab)
    if i < 0 or i >= len(st.session_state.acoes):
        i = 0
        st.session_state.active_tab = 0

    col_a, col_b, col_c = st.columns(3)
    col_a.text_input(
        "Ticker",
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
    if ticker_atual:
        chave = ticker_atual.upper().removesuffix(".SA")
        cache = st.session_state.setdefault("empresa_cache", {})
        if chave not in cache:
            with st.spinner("Buscando descrição da empresa…"):
                empresa = _obter_empresa(ticker_atual)
        else:
            empresa = _obter_empresa(ticker_atual)
        prov_txt = str(st.session_state.get(f"proventos_{i}", "") or "")
        _render_empresa(empresa, ticker=ticker_atual, proventos=prov_txt)
        _render_grafico_preco(ticker_atual, i)

    status_bits = []
    for key in (f"save_msg_{i}", f"preco_msg_{i}", f"proventos_msg_{i}"):
        msg = st.session_state.pop(key, None)
        if msg:
            status_bits.append(msg)
    if status_bits:
        st.caption(" · ".join(status_bits))

    for aviso in st.session_state.pop(f"proventos_avisos_{i}", []) or []:
        st.warning(aviso)

    with st.expander("Opções", expanded=False):
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

    # Sincroniza todas as abas (ativa via widgets; demais via estado)
    st.session_state.acoes = _snapshot_acoes_from_widgets()

    # Auto-salvar a aba ativa após edições manuais
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
            if j == i:
                st.info(
                    "Digite um ticker e pressione Enter — preço e proventos "
                    "são buscados automaticamente."
                )
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
        st.subheader("Comparativo")
        empresas = _empresas_mapa()

        def _linha_comparativo(r: ValuationResult) -> dict:
            row = {
                "Setor": rotulo_setor(setor_do_ticker(r.ticker, empresas)),
                "Ticker": r.ticker,
                "Base líquida": round(r.base, 6) if r.base is not None else None,
                "DY atual": round(r.dy_atual * 100, 2) if r.dy_atual is not None else None,
                "Teto (seu DY)": round(r.preco_teto, 4) if r.preco_teto is not None else None,
                "Preço atual": round(r.preco_atual, 4),
                "% vs teto": round(r.diferenca * 100, 2) if r.diferenca is not None else None,
                "Veredito": r.veredito,
            }
            if r.base is not None and r.base > 0 and r.preco_atual > 0:
                baz = conta_bazin(r.base, r.preco_atual)
                row["Teto Bazin"] = round(baz.preco_justo or 0, 4)
                row["% Bazin"] = (
                    round(baz.diferenca * 100, 2) if baz.diferenca is not None else None
                )
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
                    row["Justo Graham"] = round(gra.preco_justo or 0, 4)
                    row["% Graham"] = (
                        round(gra.diferenca * 100, 2)
                        if gra.diferenca is not None
                        else None
                    )
                except Exception:  # noqa: BLE001
                    pass
            return row

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
            st.dataframe(
                [_linha_comparativo(r) for r in grupo],
                use_container_width=True,
                hide_index=True,
            )
        st.caption(
            "% vs teto negativo = barata (abaixo do teto). Dentro de cada setor, "
            "ordenado da mais barata para a mais cara."
        )

    _rodar_auto_precos()


if __name__ == "__main__":
    main()
