"""Tema claro/escuro e componentes HTML de hierarquia visual."""

from __future__ import annotations

import html
from typing import Literal

import streamlit as st

TemaNome = Literal["claro", "escuro"]
Tom = Literal["ok", "warn", "bad", "neutral"]

TEMAS: dict[str, dict[str, str]] = {
    "claro": {
        "bg": "#eef3f2",
        "bg2": "#e2eaea",
        "surface": "#f7faf9",
        "surface2": "#e8efee",
        "border": "rgba(15, 40, 45, 0.12)",
        "text": "#0f1f24",
        "muted": "#5a6f76",
        "accent": "#0d9488",
        "accent_soft": "rgba(13, 148, 136, 0.14)",
        "ok": "#0f766e",
        "ok_bg": "rgba(15, 118, 110, 0.10)",
        "warn": "#a16207",
        "warn_bg": "rgba(161, 98, 7, 0.10)",
        "bad": "#b91c1c",
        "bad_bg": "rgba(185, 28, 28, 0.09)",
        "fila_bg": "rgba(247, 250, 249, 0.9)",
        "fila_chip_bg": "rgba(13, 148, 136, 0.10)",
        "fila_chip_border": "rgba(13, 148, 136, 0.20)",
        "fila_chip_text": "#0f1f24",
        "fila_setor": "#5a6f76",
        "sidebar_bg": "#e4eceb",
        "input_bg": "#f7faf9",
        "btn_bg": "#e4eceb",
        "font": '"DM Sans", system-ui, sans-serif',
        "mono": '"IBM Plex Mono", ui-monospace, monospace',
        "shadow": "0 1px 2px rgba(15, 31, 36, 0.04), 0 8px 24px rgba(15, 31, 36, 0.05)",
    },
    "escuro": {
        "bg": "#0c1214",
        "bg2": "#111a1d",
        "surface": "#152025",
        "surface2": "#1a282e",
        "border": "rgba(148, 180, 188, 0.16)",
        "text": "#e6eef0",
        "muted": "#8aa0a8",
        "accent": "#2dd4bf",
        "accent_soft": "rgba(45, 212, 191, 0.14)",
        "ok": "#34d399",
        "ok_bg": "rgba(52, 211, 153, 0.12)",
        "warn": "#fbbf24",
        "warn_bg": "rgba(251, 191, 36, 0.12)",
        "bad": "#f87171",
        "bad_bg": "rgba(248, 113, 113, 0.12)",
        "fila_bg": "rgba(21, 32, 37, 0.85)",
        "fila_chip_bg": "rgba(12, 18, 20, 0.55)",
        "fila_chip_border": "rgba(148, 180, 188, 0.16)",
        "fila_chip_text": "#d5e2e6",
        "fila_setor": "#8aa0a8",
        "sidebar_bg": "#0f171a",
        "input_bg": "#1a282e",
        "btn_bg": "#1e2c32",
        "font": '"DM Sans", system-ui, sans-serif',
        "mono": '"IBM Plex Mono", ui-monospace, monospace',
        "shadow": "0 1px 2px rgba(0, 0, 0, 0.25), 0 12px 32px rgba(0, 0, 0, 0.35)",
    },
}


def obter_tema() -> TemaNome:
    """Tema atual: query param > session_state > claro."""
    qp = st.query_params.get("tema")
    if isinstance(qp, list):
        qp = qp[0] if qp else None
    if qp in TEMAS:
        st.session_state["tema_ui"] = qp
        return qp  # type: ignore[return-value]
    atual = st.session_state.get("tema_ui", "claro")
    if atual not in TEMAS:
        atual = "claro"
    return atual  # type: ignore[return-value]


def definir_tema(nome: str) -> None:
    if nome not in TEMAS:
        nome = "claro"
    st.session_state["tema_ui"] = nome
    st.query_params["tema"] = nome


def _css_vars(t: dict[str, str]) -> str:
    return "\n".join(f"  --{k.replace('_', '-')}: {v};" for k, v in t.items())


def inject_css(tema: TemaNome | None = None) -> None:
    """Injeta CSS global do tema (variáveis + overrides Streamlit + cards + fila)."""
    nome = tema or obter_tema()
    t = TEMAS[nome]
    vars_block = _css_vars(t)
    # Streamlit (config.toml) fica em "light"; sincronizamos os tokens nativos
    # para Emotion/chat/widgets não ficarem claros no modo escuro.
    color_scheme = "dark" if nome == "escuro" else "light"
    css = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root {{
{vars_block}
  /* tokens nativos do Streamlit (sobrescrevem theme.base=light) */
  --background-color: {t["bg"]} !important;
  --secondary-background-color: {t["surface"]} !important;
  --text-color: {t["text"]} !important;
  --primary-color: {t["accent"]} !important;
  color-scheme: {color_scheme};
}}

html, body, [data-testid="stAppViewContainer"], .stApp {{
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: var(--font) !important;
  color-scheme: {color_scheme};
}}

[data-testid="stHeader"] {{
  background: transparent !important;
}}

[data-testid="stToolbar"] {{
  background: transparent !important;
}}

/* Sem barra lateral — navegação no topo */
section[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {{
  display: none !important;
  visibility: hidden !important;
  width: 0 !important;
  min-width: 0 !important;
}}

div[data-testid="stAppViewContainer"] > .main .block-container {{
  padding-top: 1.35rem;
  padding-bottom: 3rem;
  max-width: 1180px;
}}

h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {{
  font-family: var(--font) !important;
  letter-spacing: -0.02em;
  color: var(--text) !important;
}}

p, label, .stMarkdown, .stCaption, [data-testid="stCaption"] {{
  color: var(--text) !important;
}}

[data-testid="stCaption"] {{
  color: var(--muted) !important;
  opacity: 0.95;
}}

/* —— widgets Streamlit (mata branco residual) —— */
div[data-testid="stMetric"] {{
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 12px;
  padding: 0.75rem 0.9rem;
  box-shadow: var(--shadow);
}}

div[data-testid="stMetric"] label {{
  color: var(--muted) !important;
}}

div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
  font-family: var(--mono) !important;
  font-weight: 500;
  color: var(--text) !important;
}}

/*
 * Botões secondary: Streamlit (theme.base=light) pinta branco.
 * st.popover porta o conteúdo FORA de .stApp — seletores sem .stApp
 * e cores literais (Emotion costuma ganhar de var() em alguns builds).
 */
div[data-testid="stButton"] > button,
div[data-testid="stDownloadButton"] > button,
div[data-testid="stFormSubmitButton"] > button,
button[data-testid="stBaseButton-secondary"],
button[data-testid="baseButton-secondary"],
button[data-testid="stBaseButton-secondaryFormSubmit"],
button[kind="secondary"],
[data-testid="stPopoverBody"] button,
[data-testid="stPopoverBody"] div[data-testid="stButton"] > button,
div[data-baseweb="popover"] button,
div[data-baseweb="popover"] div[data-testid="stButton"] > button {{
  border-radius: 10px !important;
  border: 1px solid {t["border"]} !important;
  background: {t["btn_bg"]} !important;
  background-color: {t["btn_bg"]} !important;
  background-image: none !important;
  color: {t["text"]} !important;
  -webkit-text-fill-color: {t["text"]} !important;
  font-family: var(--font) !important;
  font-weight: 550 !important;
  box-shadow: none !important;
  opacity: 1 !important;
}}

div[data-testid="stButton"] > button:hover,
div[data-testid="stDownloadButton"] > button:hover,
div[data-testid="stFormSubmitButton"] > button:hover,
button[kind="secondary"]:hover,
[data-testid="stPopoverBody"] button:hover,
div[data-baseweb="popover"] button:hover {{
  border-color: {t["accent"]} !important;
  background: {t["accent_soft"]} !important;
  background-color: {t["accent_soft"]} !important;
  background-image: none !important;
  color: {t["text"]} !important;
  -webkit-text-fill-color: {t["text"]} !important;
}}

div[data-testid="stButton"] > button[kind="primary"],
div[data-testid="stFormSubmitButton"] > button[kind="primary"],
button[data-testid="stBaseButton-primary"],
button[data-testid="baseButton-primary"],
button[kind="primary"],
[data-testid="stPopoverBody"] button[kind="primary"],
div[data-baseweb="popover"] button[kind="primary"] {{
  background: {t["accent"]} !important;
  background-color: {t["accent"]} !important;
  background-image: none !important;
  color: #041012 !important;
  -webkit-text-fill-color: #041012 !important;
  border-color: transparent !important;
}}

/* rótulo do botão (span/p) — popover forçava --text claro em fundo branco */
div[data-testid="stButton"] > button p,
div[data-testid="stButton"] > button span,
div[data-testid="stDownloadButton"] > button p,
div[data-testid="stDownloadButton"] > button span,
div[data-testid="stFormSubmitButton"] > button p,
div[data-testid="stFormSubmitButton"] > button span,
[data-testid="stPopoverBody"] button p,
[data-testid="stPopoverBody"] button span,
div[data-baseweb="popover"] button p,
div[data-baseweb="popover"] button span {{
  color: inherit !important;
  -webkit-text-fill-color: inherit !important;
  background: transparent !important;
  background-color: transparent !important;
}}

[data-testid="stForm"] {{
  background: transparent !important;
  background-color: transparent !important;
  border-color: var(--border) !important;
  color: var(--text) !important;
}}

/* inputs / selects / textarea (inclui chat_input) */
div[data-baseweb="select"] > div,
div[data-baseweb="input"],
div[data-baseweb="input"] > div,
div[data-baseweb="base-input"],
div[data-baseweb="textarea"],
div[data-baseweb="textarea"] textarea,
.stTextInput input,
.stNumberInput input,
.stTextArea textarea,
.stSelectbox [data-baseweb="select"] > div,
.stMultiSelect [data-baseweb="select"] > div,
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stSelectbox"] > div > div,
[data-testid="stChatInput"] textarea,
[data-testid="stChatInputTextArea"],
[data-testid="stChatInput"] [data-baseweb="base-input"],
[data-testid="stChatInput"] [data-baseweb="textarea"],
[data-testid="stChatInput"] [data-baseweb="input"],
[data-testid="stFileUploader"] section {{
  background: var(--input-bg) !important;
  background-color: var(--input-bg) !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
  border-color: var(--border) !important;
  border-radius: 10px !important;
  caret-color: var(--text) !important;
}}

.stTextInput input::placeholder,
.stTextArea textarea::placeholder,
[data-testid="stChatInput"] textarea::placeholder,
[data-testid="stChatInputTextArea"]::placeholder {{
  color: var(--muted) !important;
  -webkit-text-fill-color: var(--muted) !important;
  opacity: 0.85;
}}

/*
 * Chat nativo do Streamlit usa Emotion + secondaryBg do theme.base=light.
 * Forçamos fundo/texto em toda a árvore do bottom bar.
 */
[data-testid="stBottom"],
[data-testid="stBottom"] > div,
[data-testid="stBottomBlockContainer"],
[data-testid="stChatInputContainer"],
div:has(> [data-testid="stChatInput"]) {{
  background: var(--bg) !important;
  background-color: var(--bg) !important;
  border-color: var(--border) !important;
  color: var(--text) !important;
  box-shadow: none !important;
}}
[data-testid="stChatInput"],
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] > div > div,
[data-testid="stChatInput"] form,
[data-testid="stChatInput"] [class*="st-emotion"],
[data-testid="stChatInput"] [class^="st-"],
[data-testid="stChatInput"] [class*=" e1"],
[data-testid="stChatInput"] div[class*="css-"] {{
  background: var(--input-bg) !important;
  background-color: var(--input-bg) !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
  border-color: var(--border) !important;
  caret-color: var(--text) !important;
}}
[data-testid="stChatInput"] {{
  border: 1px solid var(--border) !important;
  border-radius: 14px !important;
  box-shadow: none !important;
}}
[data-testid="stChatInput"] textarea,
[data-testid="stChatInput"] input,
[data-testid="stChatInputTextArea"] {{
  background: transparent !important;
  background-color: transparent !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
  caret-color: var(--text) !important;
}}
[data-testid="stChatInput"] button {{
  background: var(--accent) !important;
  background-color: var(--accent) !important;
  color: #041012 !important;
  -webkit-text-fill-color: #041012 !important;
  border: none !important;
}}
[data-testid="stChatMessage"] {{
  background: transparent !important;
  color: var(--text) !important;
}}
[data-testid="stChatMessage"] .stMarkdown,
[data-testid="stChatMessage"] p,
[data-testid="stChatMessage"] li {{
  color: var(--text) !important;
}}

/* autofill do browser não forçar fundo branco */
input:-webkit-autofill,
input:-webkit-autofill:focus,
textarea:-webkit-autofill {{
  -webkit-text-fill-color: var(--text) !important;
  caret-color: var(--text) !important;
  box-shadow: 0 0 0 1000px var(--input-bg) inset !important;
  transition: background-color 99999s ease-out;
}}

/* steppers do number_input */
.stNumberInput button,
[data-testid="stNumberInput"] button {{
  background: var(--btn-bg) !important;
  background-color: var(--btn-bg) !important;
  color: var(--text) !important;
  border-color: var(--border) !important;
}}

/* radio / checkbox */
[data-testid="stRadio"] label,
[data-testid="stCheckbox"] label {{
  color: var(--text) !important;
}}
[data-testid="stRadio"] div[role="radiogroup"] label > div:first-child,
div[data-baseweb="radio"] {{
  background-color: transparent !important;
}}

/* popovers / menus Baseweb + st.popover (portal) */
div[data-baseweb="popover"],
div[data-baseweb="popover"] > div,
div[data-baseweb="popover"] > div > div,
div[data-testid="stPopoverBody"],
div[data-testid="stPopoverBody"] > div,
div[data-baseweb="menu"],
ul[role="listbox"],
li[role="option"] {{
  background: var(--surface) !important;
  background-color: var(--surface) !important;
  color: var(--text) !important;
  border-color: var(--border) !important;
}}
div[data-testid="stPopoverBody"] {{
  border: 1px solid var(--border) !important;
  border-radius: 12px !important;
  box-shadow: var(--shadow) !important;
  padding: 0.75rem 0.9rem !important;
  min-width: min(360px, 92vw) !important;
  max-width: min(440px, 94vw) !important;
}}
div[data-testid="stPopoverBody"] p,
div[data-testid="stPopoverBody"] label,
div[data-testid="stPopoverBody"] .stMarkdown,
div[data-testid="stPopoverBody"] [data-testid="stCaption"],
div[data-testid="stPopoverBody"] [data-testid="stWidgetLabel"],
div[data-baseweb="popover"] p,
div[data-baseweb="popover"] label {{
  color: var(--text) !important;
}}
/* spans de texto do popover — não botões (senão some no Escuro) */
div[data-testid="stPopoverBody"] .stMarkdown span,
div[data-testid="stPopoverBody"] [data-testid="stCaption"] span,
div[data-baseweb="popover"] .stMarkdown span,
div[data-baseweb="popover"] [data-testid="stCaption"] span {{
  color: inherit !important;
}}
div[data-testid="stPopoverBody"] [data-testid="stCaption"],
div[data-testid="stPopoverBody"] .stCaption {{
  color: var(--muted) !important;
}}
li[role="option"]:hover {{
  background: var(--accent-soft) !important;
}}

hr {{
  border-color: var(--border) !important;
}}

/* Expander: só o container — não mexer no summary (quebra o rótulo) */
div[data-testid="stExpander"] {{
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 12px;
}}

/* dataframes / data_editor (glide) */
[data-testid="stDataFrame"],
[data-testid="stDataEditor"],
[data-testid="stDataFrame"] > div,
[data-testid="stDataEditor"] > div,
[data-testid="stDataFrameResizable"],
.dvn-scroller,
.glide-data-grid,
canvas + div {{
  background: var(--surface) !important;
  background-color: var(--surface) !important;
  border-radius: 12px;
  border-color: var(--border) !important;
}}

/* charts Altair / Vega — fundo do tema, sem caixa branca */
[data-testid="stVegaLiteChart"],
[data-testid="stArrowVegaLiteChart"],
[data-testid="stVegaLiteChart"] > div,
[data-testid="stArrowVegaLiteChart"] > div,
[data-testid="stElementContainer"]:has([data-testid="stVegaLiteChart"]),
.stPlotlyChart,
.js-plotly-plot .plotly,
.vega-embed,
.vega-embed summary,
.vega-embed canvas {{
  background: transparent !important;
  background-color: transparent !important;
  border-radius: 12px;
}}
.ui-chart-wrap {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 0.65rem 0.75rem;
  margin: 0 0 0.75rem 0;
  box-shadow: var(--shadow);
}}

.stAlert,
[data-testid="stAlert"] {{
  border-radius: 12px !important;
  border: 1px solid var(--border) !important;
  background: var(--surface) !important;
  color: var(--text) !important;
}}

/* tabs Streamlit nativas */
button[data-baseweb="tab"],
[data-testid="stTabs"] button {{
  background: transparent !important;
  color: var(--muted) !important;
}}
button[data-baseweb="tab"][aria-selected="true"] {{
  color: var(--text) !important;
  background: var(--accent-soft) !important;
}}

.top-nav {{
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.75rem 1.25rem;
  margin: 0 0 1rem 0;
  padding-bottom: 0.85rem;
  border-bottom: 1px solid var(--border);
}}

iframe[title="calculadora.tab_bar.browser_tabs"] {{
  margin-bottom: -0.55rem;
}}

/* —— page header —— */
.page-header {{
  margin: 0 0 1.15rem 0;
  padding-bottom: 0.85rem;
  border-bottom: 1px solid var(--border);
}}
.page-header h1 {{
  margin: 0 0 0.28rem 0;
  font-size: 1.65rem;
  font-weight: 650;
  letter-spacing: -0.03em;
  color: var(--text);
  line-height: 1.2;
}}
.page-header p {{
  margin: 0;
  font-size: 0.92rem;
  color: var(--muted);
  line-height: 1.45;
  max-width: 46rem;
}}

/* —— cards —— */
.ui-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 1rem 1.1rem;
  box-shadow: var(--shadow);
  margin: 0 0 0.85rem 0;
}}
.ui-card.tom-ok {{ border-left: 3px solid var(--ok); background: linear-gradient(90deg, var(--ok-bg), var(--surface) 42%); }}
.ui-card.tom-warn {{ border-left: 3px solid var(--warn); background: linear-gradient(90deg, var(--warn-bg), var(--surface) 42%); }}
.ui-card.tom-bad {{ border-left: 3px solid var(--bad); background: linear-gradient(90deg, var(--bad-bg), var(--surface) 42%); }}
.ui-card.tom-neutral {{ border-left: 3px solid var(--accent); }}

.ui-card-kicker {{
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 0.35rem 0;
}}
.ui-card-title {{
  margin: 0;
  font-size: 1.2rem;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--text);
  line-height: 1.25;
}}
.ui-card-title.tom-ok {{ color: var(--ok); }}
.ui-card-title.tom-warn {{ color: var(--warn); }}
.ui-card-title.tom-bad {{ color: var(--bad); }}
.ui-card-resumo {{
  margin: 0.45rem 0 0 0;
  font-size: 0.9rem;
  color: var(--muted);
  line-height: 1.45;
}}
.ui-card-meta {{
  margin: 0.55rem 0 0 0;
  font-size: 0.78rem;
  color: var(--muted);
  font-family: var(--mono);
}}

.ui-metrics {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 0.55rem;
  margin-top: 0.85rem;
}}
.ui-metric {{
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.55rem 0.65rem;
}}
.ui-metric-label {{
  font-size: 0.7rem;
  font-weight: 550;
  color: var(--muted);
  margin: 0 0 0.2rem 0;
}}
.ui-metric-value {{
  font-family: var(--mono);
  font-size: 0.92rem;
  font-weight: 550;
  color: var(--text);
  margin: 0;
  letter-spacing: -0.01em;
  white-space: normal;
  word-break: break-word;
  line-height: 1.35;
}}
.ui-legend {{
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  margin: 0;
  padding: 0;
  list-style: none;
}}
.ui-legend li {{
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.86rem;
  color: var(--text);
  line-height: 1.35;
}}
.ui-legend-swatch {{
  width: 0.65rem;
  height: 0.65rem;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 0.2rem;
}}
.ui-legend-name {{
  flex: 1 1 auto;
  min-width: 0;
}}
.ui-legend-meta {{
  font-family: var(--mono);
  font-size: 0.8rem;
  color: var(--muted);
  white-space: nowrap;
}}

.ui-split {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
  margin-top: 0.85rem;
}}
@media (max-width: 700px) {{
  .ui-split {{ grid-template-columns: 1fr; }}
}}
.ui-list-box {{
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.7rem 0.85rem;
}}
.ui-list-box h4 {{
  margin: 0 0 0.4rem 0;
  font-size: 0.78rem;
  font-weight: 650;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}}
.ui-list-box ul {{
  margin: 0;
  padding-left: 1.1rem;
  color: var(--text);
  font-size: 0.88rem;
  line-height: 1.45;
}}
.ui-list-box li {{ margin: 0.2rem 0; }}
.ui-list-box .empty {{
  margin: 0;
  font-size: 0.85rem;
  color: var(--muted);
}}

.ui-section-label {{
  margin: 1.35rem 0 0.65rem 0;
  font-size: 0.78rem;
  font-weight: 650;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
}}

/* —— tables —— */
.ui-table-wrap {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow);
  overflow: auto;
  margin: 0 0 0.85rem 0;
}}
.ui-table {{
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font);
  font-size: 0.86rem;
  color: var(--text);
}}
.ui-table.dense {{ font-size: 0.8rem; }}
.ui-table thead th {{
  text-align: left;
  font-size: 0.68rem;
  font-weight: 650;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted);
  padding: 0.7rem 0.85rem;
  border-bottom: 1px solid var(--border);
  background: var(--surface2);
  white-space: nowrap;
  position: sticky;
  top: 0;
}}
.ui-table tbody td {{
  padding: 0.55rem 0.85rem;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}}
.ui-table.dense tbody td {{ padding: 0.42rem 0.75rem; }}
.ui-table tbody tr:last-child td {{ border-bottom: none; }}
.ui-table tbody tr:nth-child(even) td {{ background: var(--surface2); }}
.ui-table tbody tr:hover td {{ background: var(--accent-soft); }}
.ui-table td.num {{
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
}}
.ui-table th.num {{ text-align: right; }}
.ui-table td.badge-ok {{
  font-family: var(--mono);
  color: var(--ok);
  font-weight: 600;
}}
.ui-table td.badge-mute {{
  font-family: var(--mono);
  color: var(--muted);
}}
.ui-table a.ui-link {{
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
  border-bottom: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
}}
.ui-table a.ui-link:hover {{
  color: var(--text);
  border-bottom-color: var(--text);
}}

/* —— fila —— */
.fila-acoes {{
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--fila-bg);
  margin: 0 0 0.75rem 0;
  mask-image: linear-gradient(90deg, transparent, #000 4%, #000 96%, transparent);
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 4%, #000 96%, transparent);
}}
.fila-track {{
  display: flex;
  width: max-content;
  gap: 0.45rem;
  padding: 0.45rem 0.7rem;
  animation: fila-scroll 160s linear infinite;
}}
.fila-acoes:hover .fila-track {{ animation-play-state: paused; }}
.fila-chip {{
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  white-space: nowrap;
  padding: 0.22rem 0.55rem;
  border-radius: 7px;
  font-size: 0.78rem;
  font-weight: 500;
  font-family: var(--mono);
  color: var(--fila-chip-text);
  border: 1px solid var(--fila-chip-border);
  background: var(--fila-chip-bg);
}}
.fila-dot {{
  width: 0.4rem;
  height: 0.4rem;
  border-radius: 50%;
  flex-shrink: 0;
  opacity: 0.9;
}}
.fila-setor {{
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
  padding: 0.22rem 0.5rem;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fila-setor);
  background: transparent;
  border: 1px solid var(--border);
}}
@keyframes fila-scroll {{
  from {{ transform: translateX(0); }}
  to {{ transform: translateX(-50%); }}
}}
@media (prefers-reduced-motion: reduce) {{
  .fila-track {{ animation: none; flex-wrap: wrap; width: 100%; }}
}}
</style>
"""
    # st.html costuma ganhar da folha Emotion do Streamlit; markdown fica de fallback.
    if hasattr(st, "html"):
        st.html(css)
    else:
        st.markdown(css, unsafe_allow_html=True)


def tokens_tema(tema: TemaNome | None = None) -> dict[str, str]:
    return TEMAS[tema or obter_tema()]


def inject_css_popover(tema: TemaNome | None = None) -> None:
    """CSS extra no portal do st.popover (fora de .stApp o Emotion ganha fácil)."""
    t = TEMAS[tema or obter_tema()]
    css = f"""
<style>
[data-testid="stPopoverBody"] button,
[data-testid="stPopoverBody"] [data-testid="stButton"] > button,
div[data-baseweb="popover"] button {{
  background: {t["btn_bg"]} !important;
  background-color: {t["btn_bg"]} !important;
  background-image: none !important;
  color: {t["text"]} !important;
  -webkit-text-fill-color: {t["text"]} !important;
  border: 1px solid {t["border"]} !important;
}}
[data-testid="stPopoverBody"] button span,
[data-testid="stPopoverBody"] button p,
div[data-baseweb="popover"] button span,
div[data-baseweb="popover"] button p {{
  color: {t["text"]} !important;
  -webkit-text-fill-color: {t["text"]} !important;
}}
</style>
"""
    if hasattr(st, "html"):
        st.html(css)
    else:
        st.markdown(css, unsafe_allow_html=True)


# Cores distintas o bastante para ler a pizza, sem “carnaval”.
_PALETA_SETOR = (
    "#0d9488",  # teal
    "#2563eb",  # azul
    "#d97706",  # âmbar
    "#64748b",  # slate
    "#e11d48",  # rose
    "#0891b2",  # ciano
    "#78716c",  # stone
    "#4f46e5",  # indigo
)


def _altair_base_config(chart, *, height: int | None = None):
    import altair as alt

    t = tokens_tema()
    configured = (
        chart.configure(background="transparent")
        .configure_view(strokeWidth=0, fill="transparent")
        .configure_axis(
            labelColor=t["muted"],
            titleColor=t["muted"],
            gridColor=t["border"],
            domainColor=t["border"],
            tickColor=t["border"],
        )
        .configure_legend(
            labelColor=t["text"],
            titleColor=t["muted"],
            orient="right",
        )
        .configure_title(color=t["text"])
    )
    if height:
        configured = configured.properties(height=height)
    return configured


def _render_altair(chart, *, height: int | None = None) -> None:
    st.altair_chart(
        _altair_base_config(chart, height=height),
        use_container_width=True,
    )


def chart_linha(
    df,
    *,
    y_col: str,
    height: int = 240,
) -> None:
    """Line chart Altair com fundo transparente (claro/escuro)."""
    import altair as alt

    if df is None or len(df) == 0:
        return
    plot = df.reset_index()
    x_col = plot.columns[0]
    t = tokens_tema()
    chart = (
        alt.Chart(plot)
        .mark_line(color=t["accent"], strokeWidth=2)
        .encode(
            x=alt.X(f"{x_col}:T", title=None),
            y=alt.Y(f"{y_col}:Q", title=y_col),
            tooltip=[
                alt.Tooltip(f"{x_col}:T", title="Data"),
                alt.Tooltip(f"{y_col}:Q", title=y_col, format=".2f"),
            ],
        )
    )
    _render_altair(chart, height=height)


def chart_barras_horiz(
    dados: dict[str, float | int],
    *,
    height: int = 220,
) -> None:
    """Bar chart horizontal temático."""
    import altair as alt
    import pandas as pd

    if not dados:
        return
    t = tokens_tema()
    df = pd.DataFrame({"label": list(dados.keys()), "valor": list(dados.values())})
    chart = (
        alt.Chart(df)
        .mark_bar(color=t["accent"], cornerRadiusEnd=4)
        .encode(
            y=alt.Y("label:N", sort=None, title=None),
            x=alt.X("valor:Q", title=None),
            tooltip=["label:N", "valor:Q"],
        )
    )
    _render_altair(chart, height=height)


def metricas_grid(metricas: list[tuple[str, str]]) -> None:
    """Grid de métricas HTML (não trunca como st.metric)."""
    st.markdown(_metricas_html(metricas), unsafe_allow_html=True)


def _fmt_brl_curto(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def chart_pizza_setores(
    fatias: list[tuple[str, float]],
    *,
    height: int = 260,
) -> None:
    """Donut + legenda HTML com % e valor (cores distintas, sem carnaval)."""
    import altair as alt
    import pandas as pd

    limpas = sorted(
        [(s, float(v)) for s, v in fatias if v and v > 0],
        key=lambda kv: -kv[1],
    )
    if not limpas:
        st.caption("Sem valores de mercado para montar o gráfico por setor.")
        return
    total = sum(v for _, v in limpas)
    setores = [s for s, _ in limpas]
    cores = [_PALETA_SETOR[i % len(_PALETA_SETOR)] for i in range(len(setores))]
    df = pd.DataFrame(
        {
            "setor": setores,
            "valor": [v for _, v in limpas],
            "pct": [v / total for _, v in limpas],
        }
    )
    t = tokens_tema()
    chart = (
        alt.Chart(df)
        .mark_arc(innerRadius=58, strokeWidth=1.5, stroke=t["bg"])
        .encode(
            theta=alt.Theta("valor:Q", stack=True),
            color=alt.Color(
                "setor:N",
                scale=alt.Scale(domain=setores, range=cores),
                legend=None,
            ),
            order=alt.Order("valor:Q", sort="descending"),
            tooltip=[
                alt.Tooltip("setor:N", title="Setor"),
                alt.Tooltip("valor:Q", title="R$", format=",.2f"),
                alt.Tooltip("pct:Q", title="%", format=".1%"),
            ],
        )
        .properties(width=220, height=height)
    )

    col_chart, col_leg = st.columns([1.05, 1.2])
    with col_chart:
        st.altair_chart(
            _altair_base_config(chart, height=height),
            use_container_width=True,
        )
    with col_leg:
        itens = []
        for i, (setor, valor) in enumerate(limpas):
            pct = valor / total * 100.0
            cor = cores[i]
            itens.append(
                "<li>"
                f'<span class="ui-legend-swatch" style="background:{html.escape(cor)}"></span>'
                f'<span class="ui-legend-name">{html.escape(setor)}</span>'
                f'<span class="ui-legend-meta">'
                f"{pct:.1f}% · {html.escape(_fmt_brl_curto(valor))}"
                "</span>"
                "</li>"
            )
        st.markdown(
            f'<ul class="ui-legend">{"".join(itens)}</ul>',
            unsafe_allow_html=True,
        )


def page_header(titulo: str, apoio: str) -> None:
    st.markdown(
        f'<div class="page-header"><h1>{html.escape(titulo)}</h1>'
        f"<p>{html.escape(apoio)}</p></div>",
        unsafe_allow_html=True,
    )


def section_label(texto: str) -> None:
    st.markdown(
        f'<div class="ui-section-label">{html.escape(texto)}</div>',
        unsafe_allow_html=True,
    )


def _metricas_html(metricas: list[tuple[str, str]]) -> str:
    parts = []
    for label, value in metricas:
        parts.append(
            '<div class="ui-metric">'
            f'<div class="ui-metric-label">{html.escape(label)}</div>'
            f'<div class="ui-metric-value">{html.escape(value)}</div>'
            "</div>"
        )
    return f'<div class="ui-metrics">{"".join(parts)}</div>' if parts else ""


def card_decisao(
    titulo: str,
    resumo: str,
    tom: Tom,
    *,
    kicker: str = "Tese",
    metricas: list[tuple[str, str]] | None = None,
) -> None:
    body = (
        f'<div class="ui-card tom-{tom}">'
        f'<div class="ui-card-kicker">{html.escape(kicker)}</div>'
        f'<p class="ui-card-title tom-{tom}">{html.escape(titulo)}</p>'
        f'<p class="ui-card-resumo">{html.escape(resumo)}</p>'
        f"{_metricas_html(metricas or [])}"
        "</div>"
    )
    st.markdown(body, unsafe_allow_html=True)


def card_conta(
    nome: str,
    veredito: str,
    tom: Tom,
    metricas: list[tuple[str, str]],
    *,
    detalhe: str = "",
) -> None:
    meta = (
        f'<p class="ui-card-meta">{html.escape(detalhe)}</p>' if detalhe else ""
    )
    body = (
        f'<div class="ui-card tom-{tom}">'
        f'<div class="ui-card-kicker">{html.escape(nome)}</div>'
        f'<p class="ui-card-title tom-{tom}">{html.escape(veredito)}</p>'
        f"{_metricas_html(metricas)}"
        f"{meta}"
        "</div>"
    )
    st.markdown(body, unsafe_allow_html=True)


def card_secao(
    titulo: str,
    *,
    metricas: list[tuple[str, str]] | None = None,
    meta: str = "",
    tom: Tom = "neutral",
) -> None:
    meta_html = f'<p class="ui-card-meta">{html.escape(meta)}</p>' if meta else ""
    body = (
        f'<div class="ui-card tom-{tom}">'
        f'<div class="ui-card-kicker">{html.escape(titulo)}</div>'
        f"{_metricas_html(metricas or [])}"
        f"{meta_html}"
        "</div>"
    )
    st.markdown(body, unsafe_allow_html=True)


def card_listas(
    a_favor: list[str],
    contra: list[str],
    *,
    titulo_ok: str = "Por que comprar",
    titulo_no: str = "Por que não / cuidado",
) -> None:
    def _lista(titulo: str, itens: list[str], vazio: str) -> str:
        if itens:
            lis = "".join(f"<li>{html.escape(t)}</li>" for t in itens)
            corpo = f"<ul>{lis}</ul>"
        else:
            corpo = f'<p class="empty">{html.escape(vazio)}</p>'
        return (
            f'<div class="ui-list-box"><h4>{html.escape(titulo)}</h4>{corpo}</div>'
        )

    body = (
        '<div class="ui-split">'
        f'{_lista(titulo_ok, a_favor, "Nenhum ponto forte claro agora.")}'
        f'{_lista(titulo_no, contra, "Sem alertas principais.")}'
        "</div>"
    )
    st.markdown(body, unsafe_allow_html=True)


def tom_de_diferenca(diferenca: float | None) -> Tom:
    if diferenca is None:
        return "neutral"
    if diferenca < 0:
        return "ok"
    if diferenca > 0:
        return "bad"
    return "neutral"


def tom_de_decisao(decisao: str) -> Tom:
    if decisao == "compra":
        return "ok"
    if decisao == "cautela":
        return "bad"
    return "warn"


def _cell_str(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "✓" if value else "—"
    if isinstance(value, float):
        # formatação leve; preferir string já formatada pelo caller
        txt = f"{value:g}"
        return txt.replace(".", ",")
    return str(value)


def html_table(
    headers: list[str],
    rows: list[list[object]],
    *,
    numeric_cols: list[int] | None = None,
    dense: bool = True,
    link_cols: dict[int, str] | None = None,
) -> None:
    """Tabela HTML temática (claro/escuro). Células são escapadas.

    ``link_cols``: índice da coluna → nome do query param (ex.: ``{0: "abrir"}``)
    gera ``?abrir=TICKER`` para abrir na Calculadora.
    """
    nums = set(numeric_cols or [])
    links = link_cols or {}
    ths = []
    for i, h in enumerate(headers):
        cls = ' class="num"' if i in nums else ""
        ths.append(f"<th{cls}>{html.escape(str(h))}</th>")
    body_rows = []
    for row in rows:
        tds = []
        for i, cell in enumerate(row):
            raw = _cell_str(cell)
            escaped = html.escape(raw)
            if i in links and raw and raw != "—":
                param = html.escape(links[i], quote=True)
                val = html.escape(raw, quote=True)
                inner = (
                    f'<a class="ui-link" href="?{param}={val}" '
                    f'title="Abrir {escaped} na Calculadora">{escaped}</a>'
                )
                tds.append(f"<td>{inner}</td>")
            elif i in nums:
                tds.append(f'<td class="num">{escaped}</td>')
            elif raw == "✓":
                tds.append(f'<td class="badge-ok">{escaped}</td>')
            elif raw == "—" and i == len(headers) - 1 and "base" in headers[-1].lower():
                tds.append(f'<td class="badge-mute">{escaped}</td>')
            else:
                tds.append(f"<td>{escaped}</td>")
        body_rows.append(f"<tr>{''.join(tds)}</tr>")
    dense_cls = " dense" if dense else ""
    markup = (
        f'<div class="ui-table-wrap"><table class="ui-table{dense_cls}">'
        f"<thead><tr>{''.join(ths)}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
    )
    st.markdown(markup, unsafe_allow_html=True)


def html_table_dicts(
    rows: list[dict],
    *,
    columns: list[str] | None = None,
    numeric_cols: list[str] | None = None,
    dense: bool = True,
) -> None:
    """Atalho: lista de dicts → html_table na ordem das colunas."""
    if not rows and not columns:
        return
    cols = columns or list(rows[0].keys())
    num_names = set(numeric_cols or [])
    num_idx = [i for i, c in enumerate(cols) if c in num_names]
    data = [[r.get(c) for c in cols] for r in rows]
    html_table(cols, data, numeric_cols=num_idx, dense=dense)
