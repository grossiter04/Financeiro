"""Cliente OkaneBox para preço e proventos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

BASE_URL = "https://www.okanebox.com.br"


@dataclass
class OkaneQuote:
    ticker: str
    preco: float | None
    texto_proventos: str
    avisos: list[str]


def _auth_headers(token: str | None) -> dict[str, str]:
    """
    Premium OkaneBox: Authorization: Bearer <e-mail do plano>.
    Aceita 'Bearer email' completo ou só o e-mail.
    """
    if not token:
        return {}
    raw = token.strip()
    if not raw.lower().startswith("bearer "):
        raw = f"Bearer {raw}"
    return {"Authorization": raw, "Accept-Encoding": "gzip"}


def _fmt_date(raw: str | None) -> str:
    if not raw:
        return ""
    raw = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return ""


def _fmt_valor(valor: float) -> str:
    return f"{valor:.8f}".replace(".", ",")


def _label_to_tipo(label: str) -> str | None:
    upper = (label or "").upper()
    if "JCP" in upper or "JUROS" in upper:
        return "JCP"
    if "TRIBUT" in upper and "REND" in upper:
        return "Rend. Tributado"
    if "DIVID" in upper:
        return "Dividendo"
    if "RENDIMENTO" in upper or upper.startswith("REND"):
        # Rendimento sem "tributado": trate como isento (FII típico)
        return "Dividendo"
    return None


def _parse_json_or_message(resp: httpx.Response) -> object:
    text = resp.text.strip()
    if text.startswith("Necessario Token") or text.startswith("Necessário Token"):
        raise ValueError(
            "OkaneBox pediu token. No campo da barra lateral, use o e-mail "
            "do plano premium (Authorization: Bearer seu@email.com)."
        )
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Resposta inválida da OkaneBox: {text[:200]}") from exc


def _fetch_preco_ultima(client: httpx.Client, ticker: str, token: str | None) -> float | None:
    url = f"{BASE_URL}/api/acoes/ultima/{ticker}/"
    resp = client.get(url, headers=_auth_headers(token))
    resp.raise_for_status()
    data = _parse_json_or_message(resp)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return None
    preco = data.get("PREULT")
    return float(preco) if preco is not None else None


def _fetch_preco_hist_gratis(client: httpx.Client, ticker: str) -> tuple[float | None, str | None]:
    """Fallback gratuito: último fechamento disponível (atraso ~15 dias)."""
    fim = date.today() - timedelta(days=15)
    ini = fim - timedelta(days=40)
    url = (
        f"{BASE_URL}/api/acoes/hist/{ticker}/"
        f"{ini.strftime('%Y%m%d')}/{fim.strftime('%Y%m%d')}/"
    )
    resp = client.get(url)
    resp.raise_for_status()
    data = _parse_json_or_message(resp)
    if not isinstance(data, list) or not data:
        return None, None
    last = data[-1]
    preco = last.get("PREULT")
    dat = _fmt_date(last.get("DATPRG"))
    return (float(preco) if preco is not None else None), dat


def _fetch_proventos(client: httpx.Client, ticker: str, token: str | None) -> list[dict]:
    url = f"{BASE_URL}/api/acoes/proventos/{ticker}/"
    resp = client.get(url, headers=_auth_headers(token))
    resp.raise_for_status()
    data = _parse_json_or_message(resp)
    if not isinstance(data, list):
        raise ValueError(f"Formato inesperado de proventos: {type(data).__name__}")
    return data


def fetch_quote(
    ticker: str,
    token: str | None = None,
    *,
    timeout: float = 30.0,
) -> OkaneQuote:
    """
    Busca proventos e preço na OkaneBox.
    Proventos e última cotação exigem token (e-mail do plano).
    Sem token, tenta só o preço histórico gratuito (atrasado).
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker vazio")

    avisos: list[str] = []
    preco: float | None = None
    linhas: list[str] = []

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        if token:
            try:
                cash = _fetch_proventos(client, ticker, token)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Falha ao buscar proventos: {exc}") from exc

            for item in cash:
                tipo = _label_to_tipo(str(item.get("TIPO_PROVENTO") or ""))
                if tipo is None:
                    avisos.append(f"Ignorado tipo: {item.get('TIPO_PROVENTO')!r}")
                    continue
                data_com = _fmt_date(item.get("DATA_COM") or item.get("DATA_APROVACAO"))
                data_pag = _fmt_date(item.get("DATA_PAGAMENTO") or item.get("DATA_COM"))
                valor = item.get("VALOR")
                if valor is None or not data_com or not data_pag:
                    avisos.append("Provento incompleto ignorado.")
                    continue
                linhas.append(f"{tipo}\t{data_com}\t{data_pag}\t{_fmt_valor(float(valor))}")

            if cash and not linhas:
                avisos.append("A API retornou proventos, mas nenhum pôde ser convertido.")

            try:
                preco = _fetch_preco_ultima(client, ticker, token)
            except Exception as exc:  # noqa: BLE001
                avisos.append(f"Não foi possível obter a última cotação: {exc}")
                preco, dat = _fetch_preco_hist_gratis(client, ticker)
                if preco is not None:
                    avisos.append(
                        f"Usando preço histórico gratuito{f' de {dat}' if dat else ''} "
                        "(atraso ~15 dias)."
                    )
        else:
            avisos.append(
                "Sem token OkaneBox: proventos não vêm na versão gratuita. "
                "Informe o e-mail do plano premium na barra lateral, "
                "ou cole os proventos manualmente."
            )
            preco, dat = _fetch_preco_hist_gratis(client, ticker)
            if preco is not None:
                avisos.append(
                    f"Preço histórico gratuito{f' de {dat}' if dat else ''} "
                    "(atraso ~15 dias)."
                )
            else:
                avisos.append("Não foi possível obter preço sem token.")

    return OkaneQuote(
        ticker=ticker,
        preco=preco,
        texto_proventos="\n".join(linhas),
        avisos=avisos,
    )
