"""Calculadora de preço teto por proventos — interface Streamlit."""

from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Streamlit roda este arquivo como script: a raiz do projeto precisa estar no path.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

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
)
from calculadora.empresa import EmpresaInfo, fetch_empresa
from calculadora.fundamentos import Fundamentos, fetch_fundamentos
from calculadora.preco import RANGES_HISTORICO, fetch_historico, fetch_preco, logo_url
from calculadora.proventos_fetch import fetch_proventos
from calculadora.storage import excluir_acao, listar_acoes, obter_acao, salvar_acao
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
    """Selectbox compacto (como antes), com logo pequeno ao lado do ticker escolhido."""
    tickers = [a.ticker for a in salvas]

    def _abrir_salva_callback() -> None:
        ticker = st.session_state.get("sidebar_abrir_ticker")
        if ticker:
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
            options=tickers,
            index=None,
            placeholder="Escolha uma ação…",
            key="sidebar_abrir_ticker",
            on_change=_abrir_salva_callback,
            help="Selecione para abrir na calculadora.",
        )
    st.caption(f"{len(salvas)} salva(s)")


def _render_empresa(info: EmpresaInfo | None, *, ticker: str) -> None:
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
            return
        st.markdown(f"**{info.resumo}**")
        if info.descricao:
            st.caption(info.descricao)
        st.caption(f"Fonte: {info.fonte}")


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
    """Remove o padding extra do iframe das abas."""
    st.markdown(
        """
        <style>
        iframe[title="calculadora.tab_bar.browser_tabs"] {
          margin-bottom: -0.6rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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

    with col_b:
        if result.base is not None and result.base > 0 and result.preco_atual > 0:
            _render_conta_card(conta_bazin(result.base, result.preco_atual))
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
                _render_conta_card(
                    conta_graham(fund.lpa, fund.vpa, result.preco_atual)
                )
                st.caption(f"LPA {fund.lpa:.4f} · VPA {fund.vpa:.4f} · {fund.fonte}")
            except Exception as exc:  # noqa: BLE001
                st.caption(f"Graham: {exc}")


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


def main() -> None:
    _init_state()
    _apply_pending_updates()

    st.title("Calculadora de preço teto por proventos")
    st.caption(
        "Digite o ticker: preço, proventos e descrição vêm sozinhos. "
        "A ação é salva automaticamente."
    )

    salvas = listar_acoes()

    with st.sidebar:
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

    # Garante chaves da aba ativa a partir de acoes se ainda não existirem
    for i, acao in enumerate(st.session_state.acoes):
        st.session_state.setdefault(f"ticker_{i}", acao.get("ticker", ""))
        st.session_state.setdefault(f"preco_{i}", float(acao.get("preco") or 0.0))
        st.session_state.setdefault(f"dy_{i}", float(acao.get("dy") or 6.0))
        st.session_state.setdefault(f"proventos_{i}", acao.get("proventos", ""))

    _inject_tab_css()
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
        _render_empresa(empresa, ticker=ticker_atual)
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
        ordenados = sorted(
            resultados,
            key=lambda r: (r.diferenca is None, r.diferenca if r.diferenca is not None else 0),
        )
        tabela = []
        for r in ordenados:
            row = {
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
            tabela.append(row)
        st.dataframe(tabela, use_container_width=True, hide_index=True)
        st.caption(
            "% vs teto negativo = barata (abaixo do teto). Ordenado da mais barata para a mais cara."
        )

    _rodar_auto_precos()


if __name__ == "__main__":
    main()
