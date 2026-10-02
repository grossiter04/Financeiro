"""Calculadora de preço teto por proventos — interface Streamlit."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Streamlit roda este arquivo como script: a raiz do projeto precisa estar no path.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from calculadora.core import BaseMethod, ValuationResult, YearCriterion, calculate
from calculadora.storage import excluir_acao, listar_acoes, salvar_acao
from calculadora.tab_bar import browser_tabs

try:
    from calculadora.okanebox import fetch_quote
except ImportError:  # pragma: no cover
    fetch_quote = None  # type: ignore


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
    if "okanebox_token" not in st.session_state:
        st.session_state.okanebox_token = ""
    if "active_tab" not in st.session_state:
        st.session_state.active_tab = 0


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

    event = browser_tabs(
        labels,
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
            "salvar_",
            "okanebox_",
            "exemplo_",
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


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.2f}%".replace(".", ",")


def _render_resultado(result: ValuationResult) -> None:
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
        "Cole os proventos, informe preço e DY desejado. "
        "Salve a ação para não perder os dados ao fechar o app."
    )

    salvas = listar_acoes()

    with st.sidebar:
        st.header("Ações salvas")
        if salvas:
            st.caption("Clique para abrir. ✕ exclui.")
            for acao in salvas:
                c_ticker, c_del = st.columns([5, 1])
                with c_ticker:
                    if st.button(
                        acao.ticker,
                        key=f"open_saved_{acao.ticker}",
                        use_container_width=True,
                    ):
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
                        st.rerun()
                with c_del:
                    if st.button(
                        "✕",
                        key=f"del_saved_{acao.ticker}",
                        use_container_width=True,
                        help=f"Excluir {acao.ticker}",
                    ):
                        if excluir_acao(acao.ticker):
                            st.rerun()
            st.caption(f"{len(salvas)} salva(s) em data/acoes.db")
        else:
            st.caption("Nenhuma ação salva ainda. Use **Salvar / atualizar** na aba.")

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
        st.subheader("OkaneBox (opcional)")
        st.caption(
            "Proventos exigem plano premium. Use o mesmo e-mail da compra "
            "(header Authorization: Bearer e-mail)."
        )
        st.text_input(
            "E-mail / token OkaneBox",
            key="okanebox_token",
            type="password",
            help="E-mail do plano premium. Pode ficar vazio se for colar os proventos.",
        )

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
    col_a.text_input("Ticker", key=f"ticker_{i}", placeholder="Ex.: ITUB4")
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

    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Salvar / atualizar", key=f"salvar_{i}", type="primary"):
            ticker = str(st.session_state.get(f"ticker_{i}", "")).strip()
            preco = float(st.session_state.get(f"preco_{i}", 0) or 0)
            dy = float(st.session_state.get(f"dy_{i}", 6) or 6)
            proventos = str(st.session_state.get(f"proventos_{i}", "") or "")
            try:
                salva = salvar_acao(
                    ticker=ticker,
                    preco=preco,
                    dy=dy,
                    proventos=proventos,
                )
                st.session_state[f"save_msg_{i}"] = (
                    f"Salvo: {salva.ticker} (atualizado {salva.updated_at})"
                )
                _queue_update(f"ticker_{i}", salva.ticker)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    with b2:
        if st.button("Buscar OkaneBox", key=f"okanebox_{i}"):
            ticker = str(st.session_state.get(f"ticker_{i}", "")).strip()
            if fetch_quote is None:
                st.error("httpx não instalado.")
            elif not ticker:
                st.error("Informe o ticker antes de buscar.")
            else:
                try:
                    with st.spinner("Consultando OkaneBox..."):
                        quote = fetch_quote(
                            ticker,
                            token=st.session_state.get("okanebox_token") or None,
                        )
                    if quote.preco is not None:
                        _queue_update(f"preco_{i}", float(quote.preco))
                    if quote.texto_proventos:
                        _queue_update(f"proventos_{i}", quote.texto_proventos)
                    avisos = quote.avisos[:8]
                    if len(quote.avisos) > 8:
                        avisos.append(f"... e mais {len(quote.avisos) - 8} avisos.")
                    st.session_state[f"okanebox_avisos_{i}"] = avisos
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Falha na OkaneBox: {exc}")

    with b3:
        if st.button("Colar exemplo", key=f"exemplo_{i}"):
            _queue_update(f"proventos_{i}", EXEMPLO)
            if not str(st.session_state.get(f"ticker_{i}", "")).strip():
                _queue_update(f"ticker_{i}", "EXEMPLO")
            if float(st.session_state.get(f"preco_{i}", 0) or 0) <= 0:
                _queue_update(f"preco_{i}", 10.0)
            st.rerun()

    msg = st.session_state.pop(f"save_msg_{i}", None)
    if msg:
        st.success(msg)

    st.text_area(
        "Proventos (Tipo, Data-com, Data pagamento, Valor — tab ou ;)",
        height=220,
        key=f"proventos_{i}",
        placeholder=EXEMPLO.splitlines()[0],
    )

    for aviso in st.session_state.pop(f"okanebox_avisos_{i}", []) or []:
        st.warning(aviso)

    # Sincroniza todas as abas (ativa via widgets; demais via estado)
    st.session_state.acoes = _snapshot_acoes_from_widgets()

    resultados: list[ValuationResult] = []
    for j, acao in enumerate(st.session_state.acoes):
        ticker = str(acao.get("ticker", "")).strip()
        preco = float(acao.get("preco") or 0)
        dy = float(acao.get("dy") or 6)
        proventos = str(acao.get("proventos") or "")

        if not (proventos.strip() and preco > 0):
            if j == i:
                st.info("Cole os proventos e informe um preço atual maior que zero.")
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
            _render_resultado(result)

    if len(resultados) >= 2:
        st.divider()
        st.subheader("Comparativo")
        ordenados = sorted(
            resultados,
            key=lambda r: (r.diferenca is None, r.diferenca if r.diferenca is not None else 0),
        )
        tabela = []
        for r in ordenados:
            tabela.append(
                {
                    "Ticker": r.ticker,
                    "Base líquida": round(r.base, 6) if r.base is not None else None,
                    "DY atual": round(r.dy_atual * 100, 2) if r.dy_atual is not None else None,
                    "Preço teto": round(r.preco_teto, 4) if r.preco_teto is not None else None,
                    "Preço atual": round(r.preco_atual, 4),
                    "% vs teto": round(r.diferenca * 100, 2) if r.diferenca is not None else None,
                    "Veredito": r.veredito,
                }
            )
        st.dataframe(tabela, use_container_width=True, hide_index=True)
        st.caption(
            "% vs teto negativo = barata (abaixo do teto). Ordenado da mais barata para a mais cara."
        )


if __name__ == "__main__":
    main()
