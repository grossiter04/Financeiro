"""Busca automática de proventos (Status Invest → brapi)."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

_BRT = ZoneInfo("America/Sao_Paulo")
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


@dataclass
class ProventosFetch:
    ticker: str
    texto: str
    fonte: str
    avisos: list[str]
    quantidade: int


def _fmt_valor(valor: float) -> str:
    return f"{valor:.8f}".rstrip("0").rstrip(".").replace(".", ",")


def _label_to_tipo(label: str) -> str | None:
    upper = (label or "").upper()
    if "JCP" in upper or "JUROS" in upper:
        return "JCP"
    if "TRIBUT" in upper and "REND" in upper:
        return "Rend. Tributado"
    if "DIVID" in upper:
        return "Dividendo"
    if "RENDIMENTO" in upper or upper.startswith("REND"):
        # Rendimento de FII costuma ser isento nas regras da calculadora
        return "Dividendo"
    return None


def _fmt_iso_date(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(_BRT)
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        raw_s = str(raw)[:10]
        try:
            return datetime.strptime(raw_s, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return ""


def _linhas_from_rows(
    rows: list[tuple[str, str, str, float]],
    avisos: list[str],
) -> str:
    """rows: (tipo, data_com, data_pag, valor)."""
    linhas: list[str] = []
    for tipo, data_com, data_pag, valor in rows:
        if not data_com:
            avisos.append(f"Ignorado (sem data-com): {tipo} {valor}")
            continue
        pag = data_pag if data_pag and data_pag not in {"-", "—", "–"} else data_com
        if valor < 0:
            avisos.append(f"Ignorado valor negativo em {data_com}")
            continue
        linhas.append(f"{tipo}\t{data_com}\t{pag}\t{_fmt_valor(valor)}")
    return "\n".join(linhas)


def _extract_si_list(page_html: str) -> list[dict]:
    """Extrai o JSON embutido da página do Status Invest (data-list / value)."""
    patterns = [
        r'value="(\[\{&quot;y&quot;:.*?&quot;adj&quot;:(?:true|false)\}\])"',
        r'data-list="(\[\{&quot;y&quot;:.*?&quot;adj&quot;:(?:true|false)\}\])"',
    ]
    for pat in patterns:
        m = re.search(pat, page_html, flags=re.DOTALL)
        if not m:
            continue
        raw = html.unescape(m.group(1))
        data = json.loads(raw)
        if isinstance(data, list) and data:
            return data
    raise ValueError("Status Invest: lista de proventos não encontrada na página")


def _fetch_status_invest(ticker: str, *, timeout: float) -> ProventosFetch:
    t = ticker.strip().upper()
    caminhos = (
        f"https://statusinvest.com.br/acoes/{t.lower()}",
        f"https://statusinvest.com.br/fundos-imobiliarios/{t.lower()}",
        f"https://statusinvest.com.br/fiis/{t.lower()}",
    )
    avisos: list[str] = []
    last_err: Exception | None = None

    with httpx.Client(timeout=timeout, follow_redirects=True, headers=_UA) as client:
        for url in caminhos:
            try:
                resp = client.get(url)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                if len(resp.text) < 20_000:
                    # página “vazia” / bloqueio leve
                    last_err = ValueError(f"página suspeita ({len(resp.text)} bytes)")
                    continue
                items = _extract_si_list(resp.text)
                rows: list[tuple[str, str, str, float]] = []
                for item in items:
                    tipo = _label_to_tipo(str(item.get("et") or item.get("etd") or ""))
                    if tipo is None:
                        avisos.append(f"Ignorado tipo SI: {item.get('et')!r}")
                        continue
                    data_com = str(item.get("ed") or "").strip()
                    data_pag = str(item.get("pd") or "").strip()
                    valor = item.get("v")
                    if valor is None:
                        continue
                    rows.append((tipo, data_com, data_pag, float(valor)))

                texto = _linhas_from_rows(rows, avisos)
                if not texto:
                    raise ValueError("Status Invest: nenhum provento utilizável")
                return ProventosFetch(
                    ticker=t,
                    texto=texto,
                    fonte="Status Invest",
                    avisos=avisos,
                    quantidade=texto.count("\n") + 1,
                )
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue

    raise ValueError(f"Status Invest falhou para {t}: {last_err}")


def _fetch_brapi(ticker: str, *, timeout: float) -> ProventosFetch:
    t = ticker.strip().upper()
    url = f"https://brapi.dev/api/quote/{t}"
    avisos: list[str] = []
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=_UA) as client:
        resp = client.get(url, params={"dividends": "true"})
        resp.raise_for_status()
        payload = resp.json()

    results = payload.get("results") or []
    if not results:
        raise ValueError(f"brapi: sem resultado para {t}")

    cash = ((results[0].get("dividendsData") or {}).get("cashDividends")) or []
    if not cash:
        raise ValueError(f"brapi: sem cashDividends para {t}")

    rows: list[tuple[str, str, str, float]] = []
    for item in cash:
        tipo = _label_to_tipo(str(item.get("label") or ""))
        if tipo is None:
            avisos.append(f"Ignorado tipo brapi: {item.get('label')!r}")
            continue
        data_com = _fmt_iso_date(item.get("lastDatePrior") or item.get("exDate"))
        data_pag = _fmt_iso_date(item.get("paymentDate")) or data_com
        rate = item.get("rate")
        if rate is None or not data_com:
            continue
        rows.append((tipo, data_com, data_pag, float(rate)))

    texto = _linhas_from_rows(rows, avisos)
    if not texto:
        raise ValueError("brapi: nenhum provento utilizável")
    return ProventosFetch(
        ticker=t,
        texto=texto,
        fonte="brapi.dev",
        avisos=avisos,
        quantidade=texto.count("\n") + 1,
    )


def fetch_proventos(ticker: str, *, timeout: float = 30.0) -> ProventosFetch:
    """
    Busca histórico de proventos no formato da calculadora.
    Prefere Status Invest (mesma fonte da tabela manual); se falhar, usa brapi.
    """
    t = ticker.strip().upper().removesuffix(".SA")
    if not t:
        raise ValueError("ticker vazio")
    if t == "EXEMPLO":
        raise ValueError("ticker EXEMPLO não tem proventos remotos")

    erros: list[str] = []
    try:
        return _fetch_status_invest(t, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        erros.append(f"Status Invest: {exc}")

    try:
        alt = _fetch_brapi(t, timeout=timeout)
        alt.avisos = [f"Fallback após falha: {erros[0]}", *alt.avisos]
        return alt
    except Exception as exc:  # noqa: BLE001
        erros.append(f"brapi: {exc}")

    raise ValueError(" | ".join(erros))
