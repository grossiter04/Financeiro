"""DCF de FCF (forward) e crescimento implícito (reverse DCF)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from calculadora.fundamentos import _num, _yahoo_crumb
from calculadora.preco import _simbolo_yahoo

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SELIC_PADRAO = 0.1475
PREMIO_MERCADO_PADRAO = 0.05
G_TERMINAL_PADRAO = 0.03
ANOS_PADRAO = 5


@dataclass
class FcfAno:
    ano: int
    fcf: float


@dataclass
class DadosDcf:
    ticker: str
    fcf_historico: list[FcfAno] = field(default_factory=list)
    fcf_ttm: float | None = None
    divida_total: float | None = None
    caixa: float | None = None
    acoes: float | None = None
    beta: float | None = None
    pl: float | None = None
    payout: float | None = None
    fonte: str = ""
    avisos: list[str] = field(default_factory=list)


@dataclass
class LinhaProjecaoDcf:
    ano: int
    fcf: float
    vp: float


@dataclass
class ResultadoDcf:
    ticker: str
    disponivel: bool
    preco_justo: float | None = None
    margem: float | None = None  # atual/justo − 1; negativo = barata
    wacc: float | None = None
    fcf0: float | None = None
    crescimento: float | None = None
    g_terminal: float | None = None
    anos: int = ANOS_PADRAO
    ev: float | None = None
    equity: float | None = None
    divida_liquida: float | None = None
    acoes: float | None = None
    projecao: list[LinhaProjecaoDcf] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    fonte: str = ""


@dataclass
class ResultadoReverseDcf:
    disponivel: bool
    g_implicito: float | None = None
    wacc: float | None = None
    fcf0: float | None = None
    g_historico: float | None = None
    avisos: list[str] = field(default_factory=list)


def _cagr(valores: list[float]) -> float | None:
    if len(valores) < 2:
        return None
    primeiro, ultimo = valores[0], valores[-1]
    if primeiro <= 0 or ultimo <= 0:
        return None
    n = len(valores) - 1
    try:
        return (ultimo / primeiro) ** (1 / n) - 1.0
    except (OverflowError, ZeroDivisionError, ValueError):
        return None


def _limitar(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def calcular_dcf(
    *,
    ticker: str,
    preco_atual: float,
    fcf_historico: list[float] | None = None,
    fcf_ttm: float | None = None,
    divida_total: float | None = None,
    caixa: float | None = None,
    acoes: float | None = None,
    beta: float | None = None,
    selic: float = SELIC_PADRAO,
    premio_mercado: float = PREMIO_MERCADO_PADRAO,
    crescimento: float | None = None,
    g_terminal: float | None = None,
    anos: int = ANOS_PADRAO,
    fonte: str = "",
    avisos_extra: list[str] | None = None,
) -> ResultadoDcf:
    """DCF explícito + Gordon. ``fcf_historico`` do mais antigo ao mais recente."""
    avisos = list(avisos_extra or [])
    t = (ticker or "").strip().upper() or "—"
    premio = float(premio_mercado)
    g_term = G_TERMINAL_PADRAO if g_terminal is None else float(g_terminal)
    anos = int(anos)
    beta_u = 1.0 if beta is None else float(beta)
    if beta is None:
        avisos.append("Beta ausente; usando 1,0.")

    serie = [float(v) for v in (fcf_historico or []) if v is not None]
    positivos = [v for v in serie if v > 0]
    fcf0 = float(fcf_ttm) if fcf_ttm is not None else (positivos[-1] if positivos else None)
    if fcf0 is None or fcf0 <= 0:
        return ResultadoDcf(
            ticker=t,
            disponivel=False,
            avisos=avisos + ["Sem FCF positivo suficiente para DCF."],
            fonte=fonte,
        )
    if fcf0 <= 0:
        return ResultadoDcf(
            ticker=t,
            disponivel=False,
            fcf0=fcf0,
            avisos=avisos + ["FCF base inválido."],
            fonte=fonte,
        )

    if crescimento is None:
        cagr = _cagr(positivos) if len(positivos) >= 2 else None
        if cagr is None:
            crescimento = 0.03
            avisos.append("Crescimento padrão 3% (histórico insuficiente).")
        else:
            crescimento = _limitar(cagr, 0.0, 0.15)
    else:
        crescimento = _limitar(float(crescimento), -0.05, 0.25)

    ke = float(selic) + beta_u * premio
    divida = float(divida_total or 0.0)
    cash = float(caixa or 0.0)
    dl = divida - cash
    market_cap = (
        float(preco_atual) * float(acoes)
        if preco_atual and acoes and preco_atual > 0 and acoes > 0
        else None
    )
    ev_proxy = (market_cap + dl) if market_cap is not None else None
    if ev_proxy is not None and ev_proxy > 0 and market_cap is not None:
        we = _limitar(market_cap / ev_proxy, 0.3, 1.0)
        wd = 1.0 - we
        kd = float(selic) * 0.7
        tax = 0.34
        wacc = we * ke + wd * kd * (1.0 - tax)
        avisos.append("WACC com estrutura de capital aproximada.")
    else:
        wacc = ke

    if g_term >= wacc:
        g_term = max(0.0, wacc - 0.01)
        avisos.append("g terminal ajustado para ficar abaixo do WACC.")

    proj: list[LinhaProjecaoDcf] = []
    vp_expl = 0.0
    fcf_t = fcf0
    for i in range(1, anos + 1):
        fcf_t = fcf_t * (1.0 + crescimento)
        vp = fcf_t / ((1.0 + wacc) ** i)
        vp_expl += vp
        proj.append(LinhaProjecaoDcf(ano=i, fcf=fcf_t, vp=vp))

    fcf_n1 = fcf_t * (1.0 + g_term)
    tv = fcf_n1 / (wacc - g_term)
    vp_tv = tv / ((1.0 + wacc) ** anos)
    ev = vp_expl + vp_tv
    equity = ev - dl
    if acoes is None or acoes <= 0:
        return ResultadoDcf(
            ticker=t,
            disponivel=False,
            fcf0=fcf0,
            crescimento=crescimento,
            g_terminal=g_term,
            wacc=wacc,
            anos=anos,
            ev=ev,
            equity=equity,
            divida_liquida=dl,
            projecao=proj,
            avisos=avisos + ["Sem quantidade de ações para preço justo."],
            fonte=fonte,
        )
    justo = equity / float(acoes)
    if justo <= 0:
        return ResultadoDcf(
            ticker=t,
            disponivel=False,
            fcf0=fcf0,
            crescimento=crescimento,
            g_terminal=g_term,
            wacc=wacc,
            anos=anos,
            ev=ev,
            equity=equity,
            divida_liquida=dl,
            acoes=float(acoes),
            projecao=proj,
            avisos=avisos + ["Preço justo DCF não positivo (cheque FCF/dívida)."],
            fonte=fonte,
        )
    margem = (float(preco_atual) / justo - 1.0) if preco_atual > 0 else None
    return ResultadoDcf(
        ticker=t,
        disponivel=True,
        preco_justo=justo,
        margem=margem,
        wacc=wacc,
        fcf0=fcf0,
        crescimento=crescimento,
        g_terminal=g_term,
        anos=anos,
        ev=ev,
        equity=equity,
        divida_liquida=dl,
        acoes=float(acoes),
        projecao=proj,
        avisos=avisos,
        fonte=fonte,
    )


def _preco_dcf_para_g(
    *,
    g: float,
    fcf0: float,
    wacc: float,
    g_terminal: float,
    anos: int,
    divida_liquida: float,
    acoes: float,
) -> float:
    """Preço por ação implícito sob crescimento explícito ``g``."""
    g_term = g_terminal
    if g_term >= wacc:
        g_term = max(0.0, wacc - 0.01)
    vp_expl = 0.0
    fcf_t = fcf0
    for i in range(1, anos + 1):
        fcf_t *= 1.0 + g
        vp_expl += fcf_t / ((1.0 + wacc) ** i)
    fcf_n1 = fcf_t * (1.0 + g_term)
    tv = fcf_n1 / (wacc - g_term)
    vp_tv = tv / ((1.0 + wacc) ** anos)
    equity = vp_expl + vp_tv - divida_liquida
    return equity / acoes


def crescimento_implicito(
    *,
    preco_atual: float,
    fcf0: float,
    wacc: float,
    divida_liquida: float = 0.0,
    acoes: float,
    g_terminal: float = G_TERMINAL_PADRAO,
    anos: int = ANOS_PADRAO,
    g_historico: float | None = None,
    g_min: float = -0.20,
    g_max: float = 0.40,
) -> ResultadoReverseDcf:
    """
    Reverse DCF: resolve o crescimento explícito ``g`` que iguala o preço atual.

    Soft signal — não decide sozinho; alimenta motivos da avaliação.
    """
    avisos: list[str] = []
    if preco_atual <= 0 or fcf0 <= 0 or acoes <= 0 or wacc <= 0:
        return ResultadoReverseDcf(disponivel=False, avisos=["Dados insuficientes para reverse DCF."])
    g_term = g_terminal
    if g_term >= wacc:
        g_term = max(0.0, wacc - 0.01)
        avisos.append("g terminal ajustado para ficar abaixo do WACC.")

    def preco(g: float) -> float:
        return _preco_dcf_para_g(
            g=g,
            fcf0=fcf0,
            wacc=wacc,
            g_terminal=g_term,
            anos=anos,
            divida_liquida=divida_liquida,
            acoes=acoes,
        )

    p_lo, p_hi = preco(g_min), preco(g_max)
    # Preço sobe com g; se alvo fora da faixa, satura
    if preco_atual <= p_lo:
        return ResultadoReverseDcf(
            disponivel=True,
            g_implicito=g_min,
            wacc=wacc,
            fcf0=fcf0,
            g_historico=g_historico,
            avisos=avisos + ["Preço abaixo do mínimo da faixa de g (mercado exige crescimento baixo/negativo)."],
        )
    if preco_atual >= p_hi:
        return ResultadoReverseDcf(
            disponivel=True,
            g_implicito=g_max,
            wacc=wacc,
            fcf0=fcf0,
            g_historico=g_historico,
            avisos=avisos + ["Preço acima do máximo da faixa (expectativa de crescimento muito alta)."],
        )

    lo, hi = g_min, g_max
    mid = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        p = preco(mid)
        if abs(p - preco_atual) / preco_atual < 1e-4:
            break
        if p < preco_atual:
            lo = mid
        else:
            hi = mid

    return ResultadoReverseDcf(
        disponivel=True,
        g_implicito=mid,
        wacc=wacc,
        fcf0=fcf0,
        g_historico=g_historico,
        avisos=avisos,
    )


def _parse_timeseries_fcf(payload: dict) -> list[FcfAno]:
    result = (payload.get("timeseries") or {}).get("result") or []
    out: list[FcfAno] = []
    for bloco in result:
        series_list = bloco.get("annualFreeCashFlow") or []
        for item in series_list:
            raw_date = item.get("asOfDate") or item.get("reportedValue", {}).get("fmt")
            val = _num((item.get("reportedValue") or {}).get("raw"))
            if val is None:
                val = _num(item.get("reportedValue"))
            if val is None:
                continue
            ano = None
            if isinstance(raw_date, str) and len(raw_date) >= 4 and raw_date[:4].isdigit():
                ano = int(raw_date[:4])
            elif isinstance(item.get("asOfDate"), str):
                s = item["asOfDate"]
                if len(s) >= 4 and s[:4].isdigit():
                    ano = int(s[:4])
            if ano is None:
                continue
            out.append(FcfAno(ano=ano, fcf=float(val)))
    out.sort(key=lambda x: x.ano)
    # dedupe por ano (fica o último)
    por_ano: dict[int, float] = {}
    for row in out:
        por_ano[row.ano] = row.fcf
    return [FcfAno(ano=a, fcf=v) for a, v in sorted(por_ano.items())]


def fetch_dados_dcf(ticker: str, *, timeout: float = 20.0) -> DadosDcf:
    """Busca FCF, dívida, caixa, ações e beta no Yahoo."""
    t = ticker.strip().upper().removesuffix(".SA")
    if not t:
        raise ValueError("ticker vazio")
    simbolo = _simbolo_yahoo(t)
    dados = DadosDcf(ticker=t, fonte="Yahoo Finance")
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        crumb = _yahoo_crumb(client)
        resp = client.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{simbolo}",
            params={
                "modules": "financialData,defaultKeyStatistics,summaryDetail",
                "crumb": crumb,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

    results = (payload.get("quoteSummary") or {}).get("result") or []
    if not results:
        raise ValueError("Yahoo: sem quoteSummary")
    r0 = results[0]
    fin = r0.get("financialData") or {}
    stats = r0.get("defaultKeyStatistics") or {}
    dados.fcf_ttm = _num(fin.get("freeCashflow"))
    dados.divida_total = _num(fin.get("totalDebt"))
    dados.caixa = _num(fin.get("totalCash"))
    dados.acoes = _num(stats.get("sharesOutstanding"))
    dados.beta = _num(stats.get("beta"))
    dados.pl = _num(stats.get("trailingPE"))
    dados.payout = _num(stats.get("payoutRatio"))

    agora = int(time.time())
    period1 = agora - 8 * 365 * 24 * 3600
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
            crumb = _yahoo_crumb(client)
            resp = client.get(
                f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{simbolo}",
                params={
                    "symbol": simbolo,
                    "type": "annualFreeCashFlow",
                    "period1": str(period1),
                    "period2": str(agora),
                    "crumb": crumb,
                },
            )
            if resp.status_code != 200:
                dados.avisos.append(f"Série FCF HTTP {resp.status_code}")
            else:
                dados.fcf_historico = _parse_timeseries_fcf(resp.json())
    except Exception as exc:  # noqa: BLE001
        dados.avisos.append(f"Série FCF: {exc}")

    if not dados.fcf_historico and dados.fcf_ttm is not None:
        dados.avisos.append("Usando só FCF TTM (sem série anual).")
    return dados


def dcf_do_ticker(
    ticker: str,
    *,
    preco_atual: float,
    selic: float = SELIC_PADRAO,
    premio_mercado: float = PREMIO_MERCADO_PADRAO,
    crescimento: float | None = None,
    g_terminal: float | None = None,
    anos: int = ANOS_PADRAO,
    timeout: float = 20.0,
) -> ResultadoDcf:
    """Fetch + cálculo em um passo."""
    try:
        dados = fetch_dados_dcf(ticker, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return ResultadoDcf(
            ticker=(ticker or "").strip().upper() or "—",
            disponivel=False,
            avisos=[f"Falha ao buscar dados DCF: {exc}"],
        )
    hist = [x.fcf for x in dados.fcf_historico]
    return calcular_dcf(
        ticker=dados.ticker,
        preco_atual=preco_atual,
        fcf_historico=hist,
        fcf_ttm=dados.fcf_ttm,
        divida_total=dados.divida_total,
        caixa=dados.caixa,
        acoes=dados.acoes,
        beta=dados.beta,
        selic=selic,
        premio_mercado=premio_mercado,
        crescimento=crescimento,
        g_terminal=g_terminal,
        anos=anos,
        fonte=dados.fonte,
        avisos_extra=dados.avisos,
    )


def reverse_dcf_do_ticker(
    ticker: str,
    *,
    preco_atual: float,
    selic: float = SELIC_PADRAO,
    premio_mercado: float = PREMIO_MERCADO_PADRAO,
    timeout: float = 20.0,
) -> ResultadoReverseDcf:
    """Fetch + reverse DCF (g implícito no preço)."""
    try:
        dados = fetch_dados_dcf(ticker, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return ResultadoReverseDcf(disponivel=False, avisos=[f"Falha ao buscar dados DCF: {exc}"])

    hist = [x.fcf for x in dados.fcf_historico if x.fcf > 0]
    fcf0 = dados.fcf_ttm if dados.fcf_ttm and dados.fcf_ttm > 0 else (hist[-1] if hist else None)
    if fcf0 is None or not dados.acoes or dados.acoes <= 0:
        return ResultadoReverseDcf(
            disponivel=False,
            avisos=dados.avisos + ["Sem FCF/ações para reverse DCF."],
        )

    beta_u = 1.0 if dados.beta is None else float(dados.beta)
    ke = float(selic) + beta_u * float(premio_mercado)
    divida = float(dados.divida_total or 0.0)
    cash = float(dados.caixa or 0.0)
    dl = divida - cash
    market_cap = float(preco_atual) * float(dados.acoes) if preco_atual > 0 else None
    if market_cap is not None:
        ev_proxy = market_cap + dl
        if ev_proxy > 0:
            we = _limitar(market_cap / ev_proxy, 0.3, 1.0)
            wd = 1.0 - we
            wacc = we * ke + wd * (float(selic) * 0.7) * (1.0 - 0.34)
        else:
            wacc = ke
    else:
        wacc = ke

    g_hist = _cagr(hist) if len(hist) >= 2 else None
    return crescimento_implicito(
        preco_atual=preco_atual,
        fcf0=float(fcf0),
        wacc=wacc,
        divida_liquida=dl,
        acoes=float(dados.acoes),
        g_historico=g_hist,
    )
