"""Carteira com posições reais: CSV, % do patrimônio e P&L vs preço médio."""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean

from calculadora.core import (
    YearCriterion,
    agrupar_por_ano,
    aplicar_ir,
    frequencia_meses_pagamento,
    parse_proventos,
    selecionar_anos,
)
from calculadora.preco import fetch_preco
from calculadora.proventos_fetch import fetch_proventos
from calculadora.storage import (
    PosicaoSalva,
    excluir_posicao,
    obter_acao,
    obter_posicao,
    salvar_acao,
    salvar_posicao,
)


@dataclass(frozen=True)
class Posicao:
    ticker: str
    quantidade: float
    preco_medio: float


@dataclass(frozen=True)
class PosicaoEnriquecida:
    ticker: str
    quantidade: float
    preco_medio: float
    preco_atual: float | None
    custo: float
    valor_mercado: float | None
    resultado_pct: float | None
    peso_pct: float | None
    setor: str = "outros"


_NUM_RE = re.compile(r"[^\d,.\-]")


def _parse_numero(raw: str) -> float:
    """Aceita 10.5, 10,5, 1.234,56, 1,234.56 e 1.000 (milhar BR)."""
    s = _NUM_RE.sub("", (raw or "").strip())
    if not s or s in {"-", "—"}:
        raise ValueError(f"Número inválido: {raw!r}")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return float(s)


def _norm_header(h: str) -> str:
    return (
        (h or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("é", "e")
        .replace("á", "a")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )


_ALIASES = {
    "ticker": "ticker",
    "ativo": "ticker",
    "codigo": "ticker",
    "papel": "ticker",
    "quantidade": "quantidade",
    "qtd": "quantidade",
    "qty": "quantidade",
    "preco_medio": "preco_medio",
    "precomedio": "preco_medio",
    "pm": "preco_medio",
    "preco_medio_compra": "preco_medio",
    "custo_medio": "preco_medio",
}


def parse_csv_carteira(texto: str) -> list[Posicao]:
    """Lê CSV com colunas ticker, quantidade, preco_medio (`,` ou `;`)."""
    bruto = (texto or "").strip()
    if not bruto:
        return []

    amostra = bruto.splitlines()[0]
    delim = ";" if amostra.count(";") > amostra.count(",") else ","
    reader = csv.DictReader(io.StringIO(bruto), delimiter=delim)
    if not reader.fieldnames:
        raise ValueError("CSV sem cabeçalho.")

    mapa: dict[str, str] = {}
    for h in reader.fieldnames:
        chave = _ALIASES.get(_norm_header(h))
        if chave:
            mapa[chave] = h
    for obrig in ("ticker", "quantidade", "preco_medio"):
        if obrig not in mapa:
            raise ValueError(
                "CSV precisa das colunas ticker, quantidade e preco_medio "
                f"(faltou {obrig})."
            )

    out: list[Posicao] = []
    vistos: set[str] = set()
    for i, row in enumerate(reader, start=2):
        ticker = (row.get(mapa["ticker"]) or "").strip().upper()
        if not ticker:
            continue
        try:
            qtd = _parse_numero(str(row.get(mapa["quantidade"]) or ""))
            pm = _parse_numero(str(row.get(mapa["preco_medio"]) or ""))
        except ValueError as exc:
            raise ValueError(f"Linha {i} ({ticker}): {exc}") from exc
        if qtd <= 0:
            raise ValueError(f"Linha {i}: quantidade de {ticker} deve ser > 0.")
        if pm < 0:
            raise ValueError(f"Linha {i}: preço médio de {ticker} inválido.")
        if ticker in vistos:
            raise ValueError(f"Ticker duplicado no CSV: {ticker}.")
        vistos.add(ticker)
        out.append(Posicao(ticker=ticker, quantidade=qtd, preco_medio=pm))
    return out


def export_csv_carteira(posicoes: list[Posicao] | list[PosicaoSalva]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=",", lineterminator="\n")
    w.writerow(["ticker", "quantidade", "preco_medio"])
    for p in posicoes:
        w.writerow([p.ticker, f"{p.quantidade:g}", f"{p.preco_medio:.4f}".rstrip("0").rstrip(".")])
    return buf.getvalue()


def enriquecer_posicoes(
    posicoes: list[Posicao] | list[PosicaoSalva],
    precos: dict[str, float | None],
    *,
    setores: dict[str, str] | None = None,
) -> list[PosicaoEnriquecida]:
    """Calcula custo, valor de mercado, resultado % e peso % da carteira."""
    setores = setores or {}
    parcial: list[tuple[Posicao | PosicaoSalva, float | None, float, float | None]] = []
    total_mercado = 0.0
    for p in posicoes:
        preco = precos.get(p.ticker)
        if preco is not None and preco < 0:
            preco = None
        custo = float(p.quantidade) * float(p.preco_medio)
        valor = float(p.quantidade) * float(preco) if preco is not None else None
        if valor is not None:
            total_mercado += valor
        parcial.append((p, preco, custo, valor))

    out: list[PosicaoEnriquecida] = []
    for p, preco, custo, valor in parcial:
        resultado: float | None = None
        if preco is not None and p.preco_medio > 0:
            resultado = preco / float(p.preco_medio) - 1.0
        peso: float | None = None
        if valor is not None and total_mercado > 0:
            peso = valor / total_mercado
        out.append(
            PosicaoEnriquecida(
                ticker=p.ticker,
                quantidade=float(p.quantidade),
                preco_medio=float(p.preco_medio),
                preco_atual=preco,
                custo=custo,
                valor_mercado=valor,
                resultado_pct=resultado,
                peso_pct=peso,
                setor=setores.get(p.ticker, "outros") or "outros",
            )
        )
    return out


def posicoes_de_editor(rows: list[dict]) -> list[Posicao]:
    """Converte linhas do st.data_editor em Posicao (ignora linhas vazias)."""
    out: list[Posicao] = []
    vistos: set[str] = set()
    for row in rows:
        ticker_raw = row.get("ticker")
        if ticker_raw is None or (isinstance(ticker_raw, float) and ticker_raw != ticker_raw):
            continue
        ticker = str(ticker_raw).strip().upper()
        if not ticker or ticker == "NAN":
            continue
        q_raw = row.get("quantidade")
        pm_raw = row.get("preco_medio")
        if q_raw is None or pm_raw is None or q_raw == "":
            raise ValueError(f"Preencha quantidade e preço médio de {ticker}.")
        if isinstance(q_raw, float) and q_raw != q_raw:
            raise ValueError(f"Preencha quantidade de {ticker}.")
        if isinstance(pm_raw, float) and pm_raw != pm_raw:
            raise ValueError(f"Preencha preço médio de {ticker}.")
        qtd = float(q_raw) if isinstance(q_raw, (int, float)) else _parse_numero(str(q_raw))
        pm = float(pm_raw) if isinstance(pm_raw, (int, float)) else _parse_numero(str(pm_raw))
        if qtd <= 0:
            raise ValueError(f"Quantidade de {ticker} deve ser > 0.")
        if pm < 0:
            raise ValueError(f"Preço médio de {ticker} inválido.")
        if ticker in vistos:
            raise ValueError(f"Ticker duplicado: {ticker}.")
        vistos.add(ticker)
        out.append(Posicao(ticker=ticker, quantidade=qtd, preco_medio=pm))
    return out


def preco_medio_apos_compra(
    quantidade_atual: float,
    preco_medio_atual: float,
    quantidade_compra: float,
    preco_compra: float,
) -> tuple[float, float]:
    """Retorna (nova_quantidade, novo_preco_medio) após uma compra."""
    q_atual = float(quantidade_atual)
    pm = float(preco_medio_atual)
    q_compra = float(quantidade_compra)
    p_compra = float(preco_compra)
    if q_compra <= 0:
        raise ValueError("Quantidade da compra deve ser maior que zero.")
    if p_compra < 0:
        raise ValueError("Preço da compra inválido.")
    if q_atual < 0:
        raise ValueError("Quantidade atual inválida.")
    nova_qtd = q_atual + q_compra
    if nova_qtd <= 0:
        raise ValueError("Quantidade resultante inválida.")
    if q_atual == 0:
        return nova_qtd, p_compra
    novo_pm = (q_atual * pm + q_compra * p_compra) / nova_qtd
    return nova_qtd, novo_pm


def aplicar_compra(
    *,
    ticker: str,
    quantidade: float,
    preco: float,
    db_path: Path | None = None,
) -> PosicaoSalva:
    """
    Registra uma compra: cria a posição ou recalcula o preço médio ponderado.
    """
    t = (ticker or "").strip().upper()
    if not t:
        raise ValueError("Informe o ticker.")
    atual = obter_posicao(t, db_path=db_path)
    if atual is None:
        nova_qtd, novo_pm = float(quantidade), float(preco)
        if nova_qtd <= 0:
            raise ValueError("Quantidade da compra deve ser maior que zero.")
        if novo_pm < 0:
            raise ValueError("Preço da compra inválido.")
    else:
        nova_qtd, novo_pm = preco_medio_apos_compra(
            atual.quantidade, atual.preco_medio, quantidade, preco
        )
    return salvar_posicao(
        ticker=t, quantidade=nova_qtd, preco_medio=novo_pm, db_path=db_path
    )


def aplicar_venda(
    *,
    ticker: str,
    quantidade: float,
    db_path: Path | None = None,
) -> PosicaoSalva | None:
    """
    Registra uma venda: reduz a quantidade e mantém o preço médio.
    Se zerar (ou ficar <= 0), remove a posição e retorna None.
    """
    t = (ticker or "").strip().upper()
    if not t:
        raise ValueError("Informe o ticker.")
    q_venda = float(quantidade)
    if q_venda <= 0:
        raise ValueError("Quantidade da venda deve ser maior que zero.")
    atual = obter_posicao(t, db_path=db_path)
    if atual is None:
        raise ValueError(f"Não há posição de {t} para vender.")
    nova_qtd = float(atual.quantidade) - q_venda
    if nova_qtd < -1e-9:
        raise ValueError(
            f"Venda maior que a posição: tem {atual.quantidade:g}, tentou vender {q_venda:g}."
        )
    if nova_qtd <= 1e-9:
        excluir_posicao(t, db_path=db_path)
        return None
    return salvar_posicao(
        ticker=t,
        quantidade=nova_qtd,
        preco_medio=float(atual.preco_medio),
        db_path=db_path,
    )


def garantir_acoes_das_posicoes(
    posicoes: list[Posicao] | list[PosicaoSalva],
    *,
    dy_padrao: float = 6.0,
    db_path: Path | None = None,
) -> list[str]:
    """
    Garante cada ticker em ``acoes``.

    Se ainda não existir, busca preço + proventos e salva com DY padrão.
    Retorna avisos (tickers novos e falhas de fetch).
    """
    avisos: list[str] = []
    for p in posicoes:
        t = p.ticker.strip().upper()
        if obter_acao(t, db_path=db_path) is not None:
            continue
        preco = 0.0
        proventos = ""
        try:
            cot = fetch_preco(t)
            if cot and cot.preco and cot.preco > 0:
                preco = float(cot.preco)
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"{t}: preço não encontrado ({exc})")
        try:
            got = fetch_proventos(t)
            if got and got.texto:
                proventos = got.texto
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"{t}: proventos não encontrados ({exc})")
        if preco <= 0:
            preco = float(p.preco_medio) if p.preco_medio > 0 else 0.01
        try:
            salvar_acao(
                ticker=t,
                preco=preco,
                dy=dy_padrao,
                proventos=proventos,
                db_path=db_path,
            )
            avisos.append(f"{t}: cadastrado na calculadora")
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"{t}: falha ao salvar em acoes ({exc})")
    return avisos


@dataclass(frozen=True)
class EstimativaProventoAcao:
    ticker: str
    quantidade: float
    anos_usados: tuple[int, ...]
    por_acao_medio: float | None
    por_acao_min: float | None
    por_acao_max: float | None
    por_acao_ultimo: float | None
    ano_ultimo: int | None
    total_medio: float | None
    total_min: float | None
    total_max: float | None
    total_ultimo: float | None
    aviso: str | None = None


@dataclass(frozen=True)
class EstimativaCarteira:
    n_anos: int
    por_acao: tuple[EstimativaProventoAcao, ...]
    total_medio: float
    total_min: float
    total_max: float
    total_ultimo: float

    @property
    def mensal_medio(self) -> float:
        return self.total_medio / 12.0


def estimar_proventos_acao(
    *,
    ticker: str,
    quantidade: float,
    proventos_texto: str,
    n_anos: int = 3,
) -> EstimativaProventoAcao:
    """
    Estima proventos líquidos do próximo ano para uma posição.

    Usa a média (e faixa min–máx) dos últimos ``n_anos`` anos fechados,
    agrupados por data-com — mesma lógica da calculadora, sem preço teto.
    """
    t = (ticker or "").strip().upper()
    qtd = float(quantidade)
    if qtd <= 0:
        return EstimativaProventoAcao(
            ticker=t or "—",
            quantidade=qtd,
            anos_usados=(),
            por_acao_medio=None,
            por_acao_min=None,
            por_acao_max=None,
            por_acao_ultimo=None,
            ano_ultimo=None,
            total_medio=None,
            total_min=None,
            total_max=None,
            total_ultimo=None,
            aviso="Quantidade inválida.",
        )
    if not (proventos_texto or "").strip():
        return EstimativaProventoAcao(
            ticker=t or "—",
            quantidade=qtd,
            anos_usados=(),
            por_acao_medio=None,
            por_acao_min=None,
            por_acao_max=None,
            por_acao_ultimo=None,
            ano_ultimo=None,
            total_medio=None,
            total_min=None,
            total_max=None,
            total_ultimo=None,
            aviso="Sem proventos salvos.",
        )

    proventos, erros = parse_proventos(proventos_texto)
    if not proventos:
        return EstimativaProventoAcao(
            ticker=t or "—",
            quantidade=qtd,
            anos_usados=(),
            por_acao_medio=None,
            por_acao_min=None,
            por_acao_max=None,
            por_acao_ultimo=None,
            ano_ultimo=None,
            total_medio=None,
            total_min=None,
            total_max=None,
            total_ultimo=None,
            aviso=erros[0] if erros else "Não foi possível ler os proventos.",
        )

    liquidos = aplicar_ir(proventos, criterio=YearCriterion.DATA_COM)
    resumos = agrupar_por_ano(liquidos)
    selecionados = selecionar_anos(resumos, n_ultimos=n_anos, incluir_ano_andamento=False)
    if not selecionados:
        return EstimativaProventoAcao(
            ticker=t or "—",
            quantidade=qtd,
            anos_usados=(),
            por_acao_medio=None,
            por_acao_min=None,
            por_acao_max=None,
            por_acao_ultimo=None,
            ano_ultimo=None,
            total_medio=None,
            total_min=None,
            total_max=None,
            total_ultimo=None,
            aviso="Sem anos fechados no histórico.",
        )

    valores = [r.liquido for r in selecionados]
    medio = float(mean(valores))
    minimo = float(min(valores))
    maximo = float(max(valores))
    ultimo = selecionados[-1]
    aviso = None
    if len(selecionados) < n_anos:
        aviso = f"Só {len(selecionados)} ano(s) fechado(s) (pediu {n_anos})."

    return EstimativaProventoAcao(
        ticker=t or "—",
        quantidade=qtd,
        anos_usados=tuple(r.ano for r in selecionados),
        por_acao_medio=medio,
        por_acao_min=minimo,
        por_acao_max=maximo,
        por_acao_ultimo=float(ultimo.liquido),
        ano_ultimo=ultimo.ano,
        total_medio=medio * qtd,
        total_min=minimo * qtd,
        total_max=maximo * qtd,
        total_ultimo=float(ultimo.liquido) * qtd,
        aviso=aviso,
    )


def estimar_proventos_carteira(
    posicoes: list[Posicao] | list[PosicaoSalva],
    *,
    n_anos: int = 3,
    db_path: Path | None = None,
) -> EstimativaCarteira:
    """Soma as estimativas por ticker usando proventos salvos em ``acoes``."""
    itens: list[EstimativaProventoAcao] = []
    for p in posicoes:
        salva = obter_acao(p.ticker, db_path=db_path)
        texto = salva.proventos if salva else ""
        itens.append(
            estimar_proventos_acao(
                ticker=p.ticker,
                quantidade=float(p.quantidade),
                proventos_texto=texto or "",
                n_anos=n_anos,
            )
        )

    def _soma(attr: str) -> float:
        return float(
            sum(getattr(e, attr) or 0.0 for e in itens if getattr(e, attr) is not None)
        )

    return EstimativaCarteira(
        n_anos=n_anos,
        por_acao=tuple(itens),
        total_medio=_soma("total_medio"),
        total_min=_soma("total_min"),
        total_max=_soma("total_max"),
        total_ultimo=_soma("total_ultimo"),
    )


_MESES_CURTOS = (
    "",
    "Jan",
    "Fev",
    "Mar",
    "Abr",
    "Mai",
    "Jun",
    "Jul",
    "Ago",
    "Set",
    "Out",
    "Nov",
    "Dez",
)


@dataclass(frozen=True)
class PagamentoOficial:
    data: date
    valor_bruto: float
    tipo: str


@dataclass(frozen=True)
class CalendarioProventoAcao:
    ticker: str
    oficiais: tuple[PagamentoOficial, ...]
    meses_oficiais: tuple[int, ...]
    meses_estimados: tuple[int, ...]
    aviso: str | None = None

    def marca_mes(self, mes: int) -> str:
        """● oficial · ○ estimado · vazio."""
        if mes in self.meses_oficiais:
            return "●"
        if mes in self.meses_estimados:
            return "○"
        return ""


@dataclass(frozen=True)
class CalendarioCarteira:
    por_acao: tuple[CalendarioProventoAcao, ...]
    por_mes: tuple[tuple[int, tuple[str, ...]], ...]  # (mês, tickers)


def _meses_estimados_historico(
    proventos,
    *,
    hoje: date,
    min_anos: int = 2,
) -> tuple[int, ...]:
    """
    Meses mais prováveis pelo histórico de data de pagamento.

    Conta em quantos anos-calendário distintos o mês apareceu (só passado).
    Entra na estimativa se apareceu em >= min_anos anos, ou em >=1 se o
    histórico tiver menos de min_anos anos com pagamento.
    """
    passados = [p for p in proventos if p.data_pagamento <= hoje]
    if not passados:
        return ()

    anos_por_mes: dict[int, set[int]] = defaultdict(set)
    for p in passados:
        anos_por_mes[p.data_pagamento.month].add(p.data_pagamento.year)

    n_anos_hist = len({p.data_pagamento.year for p in passados})
    limiar = 1 if n_anos_hist < min_anos else min_anos

    freq = frequencia_meses_pagamento(passados)
    escolhidos = [
        m.mes
        for m in freq
        if len(anos_por_mes[m.mes]) >= limiar
    ]
    return tuple(sorted(escolhidos))


def calendario_proventos_acao(
    *,
    ticker: str,
    proventos_texto: str,
    hoje: date | None = None,
) -> CalendarioProventoAcao:
    """Oficiais (datas futuras) + meses estimados pelo histórico."""
    t = (ticker or "").strip().upper() or "—"
    ref = hoje or date.today()
    if not (proventos_texto or "").strip():
        return CalendarioProventoAcao(
            ticker=t,
            oficiais=(),
            meses_oficiais=(),
            meses_estimados=(),
            aviso="Sem proventos salvos.",
        )

    proventos, erros = parse_proventos(proventos_texto)
    if not proventos:
        return CalendarioProventoAcao(
            ticker=t,
            oficiais=(),
            meses_oficiais=(),
            meses_estimados=(),
            aviso=erros[0] if erros else "Não foi possível ler os proventos.",
        )

    futuros = sorted(
        (p for p in proventos if p.data_pagamento > ref),
        key=lambda p: p.data_pagamento,
    )
    # Agrupa mesmo dia (dividendo+JCP no mesmo pagamento)
    por_dia: dict[date, list] = defaultdict(list)
    for p in futuros:
        por_dia[p.data_pagamento].append(p)

    oficiais: list[PagamentoOficial] = []
    for dia in sorted(por_dia):
        itens = por_dia[dia]
        bruto = sum(p.valor_bruto for p in itens)
        tipos = "+".join(sorted({p.tipo.value for p in itens}))
        oficiais.append(PagamentoOficial(data=dia, valor_bruto=bruto, tipo=tipos))

    meses_oficiais = tuple(sorted({o.data.month for o in oficiais}))
    meses_est = _meses_estimados_historico(proventos, hoje=ref)
    # Não repetir no estimado o que já tem oficial futuro neste calendário
    meses_est = tuple(m for m in meses_est if m not in meses_oficiais)

    return CalendarioProventoAcao(
        ticker=t,
        oficiais=tuple(oficiais),
        meses_oficiais=meses_oficiais,
        meses_estimados=meses_est,
        aviso=None,
    )


def calendario_proventos_carteira(
    posicoes: list[Posicao] | list[PosicaoSalva],
    *,
    db_path: Path | None = None,
    hoje: date | None = None,
) -> CalendarioCarteira:
    ref = hoje or date.today()
    itens: list[CalendarioProventoAcao] = []
    for p in posicoes:
        salva = obter_acao(p.ticker, db_path=db_path)
        texto = salva.proventos if salva else ""
        itens.append(
            calendario_proventos_acao(
                ticker=p.ticker,
                proventos_texto=texto or "",
                hoje=ref,
            )
        )

    por_mes_map: dict[int, list[str]] = {m: [] for m in range(1, 13)}
    for cal in itens:
        meses = set(cal.meses_oficiais) | set(cal.meses_estimados)
        for m in meses:
            por_mes_map[m].append(cal.ticker)

    por_mes = tuple(
        (m, tuple(tickers))
        for m, tickers in por_mes_map.items()
        if tickers
    )
    return CalendarioCarteira(por_acao=tuple(itens), por_mes=por_mes)


def rotulo_mes_curto(mes: int) -> str:
    if 1 <= mes <= 12:
        return _MESES_CURTOS[mes]
    return str(mes)
