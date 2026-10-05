"""Vigilante de preço: Bazin / Graham / DY / preço manual → Telegram."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

from calculadora.core import (
    BAZIN_DY,
    BaseMethod,
    YearCriterion,
    calculate,
    conta_bazin,
    conta_graham,
)
from calculadora.fundamentos import fetch_fundamentos
from calculadora.preco import fetch_preco
from calculadora.proventos_fetch import fetch_proventos
from calculadora.storage import listar_acoes, resolve_db_path

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCHLIST = _ROOT / "alertas" / "watchlist.toml"
DEFAULT_STATE = _ROOT / "alertas" / "state.json"


@dataclass
class WatchItem:
    ticker: str
    dy_desejado: float = 6.0
    n_anos: int = 5
    avisar_bazin: bool = True
    avisar_graham: bool = True
    avisar_dy: bool = True
    preco_maximo: float | None = None


@dataclass
class Sinal:
    ticker: str
    criterio: str
    preco: float
    teto: float
    pct: float
    detalhe: str


@dataclass
class ChecagemResultado:
    sinais: list[Sinal] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)
    enviados: list[str] = field(default_factory=list)
    ignorados_spam: list[str] = field(default_factory=list)


def load_watchlist(path: Path | None = None) -> list[WatchItem]:
    path = path or DEFAULT_WATCHLIST
    items: dict[str, WatchItem] = {}

    cfg: dict = {}
    if path.is_file():
        with path.open("rb") as f:
            cfg = tomllib.load(f) or {}

    dy_padrao = float(cfg.get("dy_padrao", 6.0))
    n_anos = int(cfg.get("n_anos", 5))
    avisar_bazin = bool(cfg.get("avisar_bazin", True))
    avisar_graham = bool(cfg.get("avisar_graham", True))
    avisar_dy = bool(cfg.get("avisar_dy", True))
    usar_banco = bool(cfg.get("usar_banco_local", True))

    for raw in cfg.get("acao") or []:
        ticker = str(raw.get("ticker", "")).strip().upper().removesuffix(".SA")
        if not ticker:
            continue
        preco_max = raw.get("preco_maximo")
        items[ticker] = WatchItem(
            ticker=ticker,
            dy_desejado=float(raw.get("dy_desejado", dy_padrao)),
            n_anos=int(raw.get("n_anos", n_anos)),
            avisar_bazin=bool(raw.get("avisar_bazin", avisar_bazin)),
            avisar_graham=bool(raw.get("avisar_graham", avisar_graham)),
            avisar_dy=bool(raw.get("avisar_dy", avisar_dy)),
            preco_maximo=float(preco_max) if preco_max not in (None, "") else None,
        )

    if usar_banco:
        db = resolve_db_path()
        if db.is_file():
            for acao in listar_acoes(db):
                t = acao.ticker.strip().upper()
                if t in items:
                    # Mantém flags do TOML; herda DY salvo se não veio override
                    continue
                items[t] = WatchItem(
                    ticker=t,
                    dy_desejado=float(acao.dy or dy_padrao),
                    n_anos=n_anos,
                    avisar_bazin=avisar_bazin,
                    avisar_graham=avisar_graham,
                    avisar_dy=avisar_dy,
                )

    return sorted(items.values(), key=lambda w: w.ticker)


def load_state(path: Path | None = None) -> dict:
    path = path or DEFAULT_STATE
    if not path.is_file():
        return {"ativos": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ativos": {}}
    if not isinstance(data, dict):
        return {"ativos": {}}
    data.setdefault("ativos", {})
    return data


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or DEFAULT_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _chave(sinal: Sinal) -> str:
    return f"{sinal.ticker}|{sinal.criterio}"


def avaliar_item(item: WatchItem, *, timeout: float = 25.0) -> tuple[list[Sinal], list[str]]:
    sinais: list[Sinal] = []
    erros: list[str] = []
    ticker = item.ticker

    try:
        cot = fetch_preco(ticker, timeout=min(timeout, 15.0))
        preco = float(cot.preco)
    except Exception as exc:  # noqa: BLE001
        return [], [f"{ticker}: preço — {exc}"]

    if item.preco_maximo is not None and item.preco_maximo > 0 and preco < item.preco_maximo:
        pct = preco / item.preco_maximo - 1.0
        sinais.append(
            Sinal(
                ticker=ticker,
                criterio="manual",
                preco=preco,
                teto=item.preco_maximo,
                pct=pct,
                detalhe=f"Abaixo do preço máximo R$ {item.preco_maximo:.4f}",
            )
        )

    base = None
    if item.avisar_bazin or item.avisar_dy:
        try:
            got = fetch_proventos(ticker, timeout=timeout)
            result = calculate(
                got.texto,
                preco_atual=preco,
                dy_desejado=item.dy_desejado / 100.0,
                ticker=ticker,
                n_ultimos=item.n_anos,
                metodo=BaseMethod.MEDIA,
                criterio=YearCriterion.DATA_COM,
                incluir_ano_andamento=False,
            )
            base = result.base
            if item.avisar_dy and result.preco_teto and result.diferenca is not None and result.diferenca < 0:
                sinais.append(
                    Sinal(
                        ticker=ticker,
                        criterio="dy",
                        preco=preco,
                        teto=result.preco_teto,
                        pct=result.diferenca,
                        detalhe=f"DY desejado {item.dy_desejado:.1f}%",
                    )
                )
            if item.avisar_bazin and base and base > 0:
                baz = conta_bazin(base, preco, dy=BAZIN_DY)
                if baz.diferenca is not None and baz.diferenca < 0:
                    sinais.append(
                        Sinal(
                            ticker=ticker,
                            criterio="bazin",
                            preco=preco,
                            teto=baz.preco_justo,
                            pct=baz.diferenca,
                            detalhe=baz.veredito,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            erros.append(f"{ticker}: proventos/Bazin/DY — {exc}")

    if item.avisar_graham:
        try:
            fund = fetch_fundamentos(ticker, timeout=min(timeout, 15.0))
            if fund.lpa and fund.vpa and fund.lpa > 0 and fund.vpa > 0:
                gra = conta_graham(fund.lpa, fund.vpa, preco)
                if gra.diferenca is not None and gra.diferenca < 0:
                    sinais.append(
                        Sinal(
                            ticker=ticker,
                            criterio="graham",
                            preco=preco,
                            teto=gra.preco_justo,
                            pct=gra.diferenca,
                            detalhe=gra.veredito,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            erros.append(f"{ticker}: Graham — {exc}")

    return sinais, erros


def formatar_mensagem(sinais: list[Sinal]) -> str:
    linhas = ["🔔 Oportunidade de compra", ""]
    por_ticker: dict[str, list[Sinal]] = {}
    for s in sinais:
        por_ticker.setdefault(s.ticker, []).append(s)
    for ticker, lista in sorted(por_ticker.items()):
        linhas.append(f"*{ticker}* — R$ {lista[0].preco:.4f}")
        for s in lista:
            linhas.append(
                f"  • {s.criterio.upper()}: {s.pct * 100:.1f}% vs teto "
                f"R$ {s.teto:.4f} ({s.detalhe})"
            )
        linhas.append("")
    return "\n".join(linhas).strip()


def enviar_telegram(texto: str, *, token: str, chat_id: str, timeout: float = 20.0) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": texto,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            raise ValueError(f"Telegram: {payload}")


def filtrar_novos(
    sinais: list[Sinal],
    state: dict,
    *,
    registrar: bool = True,
) -> tuple[list[Sinal], list[Sinal]]:
    """
    Evita spam: só reenvia se o sinal for novo ou se o preço voltou
    a ficar caro e depois barato de novo (chave saiu do state).
    """
    ativos: dict = state.setdefault("ativos", {})
    atuais = {_chave(s) for s in sinais}
    for chave in list(ativos.keys()):
        if chave not in atuais:
            del ativos[chave]

    novos: list[Sinal] = []
    repetidos: list[Sinal] = []
    for s in sinais:
        k = _chave(s)
        if k in ativos:
            repetidos.append(s)
        else:
            novos.append(s)
            if registrar:
                ativos[k] = {"preco": s.preco, "teto": s.teto, "pct": s.pct}
    return novos, repetidos


def checar_e_avisar(
    *,
    watchlist_path: Path | None = None,
    state_path: Path | None = None,
    dry_run: bool = False,
    token: str | None = None,
    chat_id: str | None = None,
) -> ChecagemResultado:
    out = ChecagemResultado()
    items = load_watchlist(watchlist_path)
    if not items:
        out.erros.append("Watchlist vazia — edite alertas/watchlist.toml")
        return out

    state = load_state(state_path)
    todos: list[Sinal] = []
    for item in items:
        sinais, erros = avaliar_item(item)
        todos.extend(sinais)
        out.erros.extend(erros)

    out.sinais = todos
    # dry-run não grava no anti-spam (senão bloqueia o aviso real depois)
    novos, repetidos = filtrar_novos(todos, state, registrar=False)
    out.ignorados_spam = [_chave(s) for s in repetidos]

    enviou = False
    if novos:
        msg = formatar_mensagem(novos)
        if dry_run:
            out.enviados = [_chave(s) for s in novos]
            print(msg)
        else:
            tok = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            cid = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            if not tok or not cid:
                out.erros.append(
                    "Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID para enviar."
                )
            else:
                enviar_telegram(msg, token=tok, chat_id=cid)
                out.enviados = [_chave(s) for s in novos]
                enviou = True

    if enviou:
        filtrar_novos(todos, state, registrar=True)
    else:
        # Só limpa chaves que deixaram de estar baratas
        filtrar_novos(todos, state, registrar=False)

    save_state(state, state_path)
    return out
