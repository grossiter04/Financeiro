"""Perfil resumido da empresa (setor + breve descrição), via brapi / Yahoo."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from calculadora.preco import _simbolo_yahoo, logo_url

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_MAX_DESC = 420


@dataclass
class EmpresaInfo:
    ticker: str
    nome: str
    setor: str
    industria: str
    descricao: str
    fonte: str
    logo_url: str = ""

    @property
    def resumo(self) -> str:
        """Uma linha com nome + setor/indústria."""
        partes = [p for p in (self.nome, self.setor, self.industria) if p]
        # evita repetir nome duas vezes se setor vazio
        vistos: list[str] = []
        for p in partes:
            if p not in vistos:
                vistos.append(p)
        return " · ".join(vistos)


def _encurtar(texto: str, limite: int = _MAX_DESC) -> str:
    texto = " ".join((texto or "").split())
    if len(texto) <= limite:
        return texto
    corte = texto[: limite + 1]
    # tenta fechar em pontuação
    for sep in (". ", "! ", "? ", "; "):
        idx = corte.rfind(sep)
        if idx >= int(limite * 0.45):
            return corte[: idx + 1].strip()
    # senão corta na última palavra
    espaco = corte.rfind(" ")
    if espaco > 0:
        return corte[:espaco].rstrip(",;") + "…"
    return corte[:limite].rstrip() + "…"


def _campo_str(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, dict):
        valor = valor.get("raw") or valor.get("fmt") or ""
    return str(valor).strip()


def _fetch_brapi(ticker: str, *, timeout: float) -> EmpresaInfo:
    url = f"https://brapi.dev/api/quote/{ticker}"
    params = {"modules": "summaryProfile"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()

    results = payload.get("results") or []
    if not results:
        msg = (payload.get("message") or payload.get("error") or "sem resultado")
        raise ValueError(f"brapi: {msg}")

    item = results[0]
    perfil = item.get("summaryProfile") or {}
    nome = _campo_str(item.get("longName") or item.get("shortName") or ticker)
    setor = _campo_str(perfil.get("sectorDisp") or perfil.get("sector"))
    industria = _campo_str(perfil.get("industryDisp") or perfil.get("industry"))
    descricao = _encurtar(_campo_str(perfil.get("longBusinessSummary")))
    if not descricao and not setor and not industria:
        raise ValueError("brapi: perfil vazio")

    return EmpresaInfo(
        ticker=ticker,
        nome=nome,
        setor=setor,
        industria=industria,
        descricao=descricao,
        fonte="brapi.dev",
        logo_url=_campo_str(item.get("logourl")) or logo_url(ticker),
    )


def _yahoo_crumb(client: httpx.Client) -> str:
    client.get("https://fc.yahoo.com")
    resp = client.get("https://query2.finance.yahoo.com/v1/test/getcrumb")
    resp.raise_for_status()
    crumb = resp.text.strip()
    if not crumb or "<" in crumb:
        raise ValueError("Yahoo: crumb inválido")
    return crumb


def _fetch_yahoo(ticker: str, *, timeout: float) -> EmpresaInfo:
    simbolo = _simbolo_yahoo(ticker)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        crumb = _yahoo_crumb(client)
        resp = client.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{simbolo}",
            params={
                "modules": "assetProfile,price,summaryProfile",
                "crumb": crumb,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

    err = (payload.get("quoteSummary") or {}).get("error")
    if err:
        raise ValueError(f"Yahoo: {err}")

    results = (payload.get("quoteSummary") or {}).get("result") or []
    if not results:
        raise ValueError(f"Yahoo: sem perfil para {simbolo}")

    bloco = results[0]
    perfil = bloco.get("assetProfile") or bloco.get("summaryProfile") or {}
    price = bloco.get("price") or {}
    nome = _campo_str(
        price.get("longName") or price.get("shortName") or price.get("symbol") or ticker
    )
    setor = _campo_str(perfil.get("sector"))
    industria = _campo_str(perfil.get("industry"))
    descricao = _encurtar(_campo_str(perfil.get("longBusinessSummary")))
    if not descricao and not setor and not industria and not nome:
        raise ValueError("Yahoo: perfil vazio")

    return EmpresaInfo(
        ticker=ticker,
        nome=nome,
        setor=setor,
        industria=industria,
        descricao=descricao,
        fonte="Yahoo Finance",
        logo_url=logo_url(ticker),
    )


def fetch_empresa(ticker: str, *, timeout: float = 15.0) -> EmpresaInfo:
    """
    Busca nome, setor/indústria e uma breve descrição da empresa.
    Prefere brapi (texto em português); se falhar, usa Yahoo Finance.
    """
    t = ticker.strip().upper().removesuffix(".SA")
    if not t:
        raise ValueError("ticker vazio")
    if t == "EXEMPLO":
        return EmpresaInfo(
            ticker=t,
            nome="Exemplo didático",
            setor="",
            industria="",
            descricao="Dados fictícios só para testar a calculadora.",
            fonte="local",
            logo_url="",
        )

    erros: list[str] = []
    try:
        return _fetch_brapi(t, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        erros.append(f"brapi: {exc}")

    try:
        return _fetch_yahoo(t, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        erros.append(f"yahoo: {exc}")

    raise ValueError(" | ".join(erros))

