"""Núcleo determinístico: parser, IR do JCP, agrupamento e valuation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from statistics import mean, median
from typing import Iterable, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------


class YearCriterion(str, Enum):
    DATA_COM = "data_com"
    DATA_PAGAMENTO = "data_pagamento"


class BaseMethod(str, Enum):
    MEDIA = "media"
    MEDIANA = "mediana"
    MINIMO = "minimo"


class ProventoTipo(str, Enum):
    DIVIDENDO = "Dividendo"
    JCP = "JCP"
    RENDIMENTO_TRIBUTADO = "Rend. Tributado"


@dataclass(frozen=True)
class AliquotaFaixa:
    desde: date
    aliquota: float


@dataclass
class Provento:
    tipo: ProventoTipo
    data_com: date
    data_pagamento: date
    valor_bruto: float
    aliquota_override: float | None = None


@dataclass
class ProventoLiquido:
    tipo: ProventoTipo
    data_com: date
    data_pagamento: date
    valor_bruto: float
    aliquota: float
    ir: float
    valor_liquido: float
    ano: int


@dataclass
class AnoResumo:
    ano: int
    bruto: float
    ir: float
    liquido: float
    parcial: bool


@dataclass
class MesFrequencia:
    mes: int
    nome: str
    quantidade: int
    percentual: float


@dataclass
class ValuationResult:
    ticker: str
    preco_atual: float
    dy_desejado: float
    anos_selecionados: list[int]
    anos_resumo: list[AnoResumo]
    base: float | None
    metodo: BaseMethod
    preco_teto: float | None
    dy_atual: float | None
    diferenca: float | None  # preco_atual / preco_teto - 1; negativo = barata
    veredito: str
    aviso: str | None = None
    erros: list[str] = field(default_factory=list)
    meses_pagamento: list[MesFrequencia] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Configuração de alíquotas
# ---------------------------------------------------------------------------


def _default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config.toml"


def _load_config(config_path: Path | None = None) -> dict:
    path = config_path or _default_config_path()
    with path.open("rb") as f:
        return tomllib.load(f)


def load_aliquotas(config_path: Path | None = None) -> list[AliquotaFaixa]:
    data = _load_config(config_path)

    faixas: list[AliquotaFaixa] = []
    for item in data.get("aliquotas_jcp", []):
        desde = date.fromisoformat(str(item["desde"]))
        faixas.append(AliquotaFaixa(desde=desde, aliquota=float(item["aliquota"])))

    if not faixas:
        faixas = [
            AliquotaFaixa(desde=date(1900, 1, 1), aliquota=0.15),
            AliquotaFaixa(desde=date(2026, 1, 1), aliquota=0.175),
        ]

    return sorted(faixas, key=lambda f: f.desde)


def aliquota_rendimento_tributado(config_path: Path | None = None) -> float:
    """
    Alíquota padrão de Rend. Tributado (configurável).
    Em muitos casos de ação/FII tributado usa-se 15% ou 20%; padrão: 15%.
    """
    try:
        data = _load_config(config_path)
        return float(data.get("aliquota_rendimento_tributado", 0.15))
    except Exception:  # noqa: BLE001
        return 0.15


def aliquota_jcp(ref: date, faixas: Sequence[AliquotaFaixa] | None = None) -> float:
    """Retorna a alíquota de IR do JCP vigente na data de referência (data-com)."""
    faixas = list(faixas) if faixas is not None else load_aliquotas()
    vigente = faixas[0].aliquota
    for faixa in faixas:
        if ref >= faixa.desde:
            vigente = faixa.aliquota
        else:
            break
    return vigente


# ---------------------------------------------------------------------------
# Datas-com (próxima / última)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasComResumo:
    ultima: date | None = None
    proxima: date | None = None

    def fmt_ultima(self) -> str:
        return self.ultima.strftime("%d/%m/%Y") if self.ultima else "—"

    def fmt_proxima(self) -> str:
        return self.proxima.strftime("%d/%m/%Y") if self.proxima else "—"

    def fmt_destaque(self) -> str:
        """Prefere a próxima; se não houver, a última."""
        if self.proxima:
            return f"próx. {self.fmt_proxima()}"
        if self.ultima:
            return f"últ. {self.fmt_ultima()}"
        return "—"


def resumo_datas_com(
    proventos_texto: str,
    *,
    hoje: date | None = None,
) -> DatasComResumo:
    """Extrai última e próxima data-com a partir do texto de proventos."""
    if not (proventos_texto or "").strip():
        return DatasComResumo()
    proventos, _ = parse_proventos(proventos_texto)
    if not proventos:
        return DatasComResumo()
    ref = hoje or date.today()
    datas = sorted({p.data_com for p in proventos})
    ultima = max((d for d in datas if d <= ref), default=None)
    proxima = min((d for d in datas if d > ref), default=None)
    return DatasComResumo(ultima=ultima, proxima=proxima)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_TIPO_MAP = {
    "dividendo": ProventoTipo.DIVIDENDO,
    "dividendos": ProventoTipo.DIVIDENDO,
    "div": ProventoTipo.DIVIDENDO,
    "jcp": ProventoTipo.JCP,
    "juros sobre capital próprio": ProventoTipo.JCP,
    "juros sobre capital proprio": ProventoTipo.JCP,
    "rend tributado": ProventoTipo.RENDIMENTO_TRIBUTADO,
    "rendimento tributado": ProventoTipo.RENDIMENTO_TRIBUTADO,
    "rendimentos tributados": ProventoTipo.RENDIMENTO_TRIBUTADO,
    "rendimento": ProventoTipo.RENDIMENTO_TRIBUTADO,
}


def _normalize_tipo(raw: str) -> str:
    """Normaliza 'Rend. Tributado', 'REND TRIBUTADO', etc."""
    return " ".join(raw.strip().lower().replace(".", " ").split())


def _parse_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"data inválida: {raw!r}")


_DATA_AUSENTE = {"", "-", "—", "–", ".", "n/a", "na", "null", "none", "s/d", "sd"}


def _parse_date_or_none(raw: str) -> date | None:
    """Aceita data normal ou marcadores de ausência (-, vazio, n/a...)."""
    texto = raw.strip().lower()
    if texto in _DATA_AUSENTE:
        return None
    return _parse_date(raw)


def _parse_number(raw: str) -> float:
    raw = raw.strip().replace("%", "")
    if "," in raw and "." in raw:
        # 1.234,56 → remove milhares
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    return float(raw)


def parse_proventos(texto: str) -> tuple[list[Provento], list[str]]:
    """
    Parseia texto colado no formato:
        Tipo  DataCom  DataPagamento  Valor  [Aliquota%]
    Separadores: tab ou ';'. Decimal com vírgula ou ponto.
    Retorna (proventos, erros_por_linha).
    """
    proventos: list[Provento] = []
    erros: list[str] = []

    for i, linha in enumerate(texto.splitlines(), start=1):
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue

        # Pula cabeçalho comum
        lower = linha.lower()
        if lower.startswith("tipo") or lower.startswith("provento"):
            continue

        if "\t" in linha:
            # Mantém colunas vazias no meio (ex.: data de pagamento em branco)
            partes = [p.strip() for p in linha.split("\t")]
            while partes and partes[-1] == "":
                partes.pop()
        else:
            partes = [p.strip() for p in linha.split(";") if p.strip()]

        # Também aceita múltiplos espaços
        if len(partes) < 4:
            partes = [p for p in linha.split() if p]

        if len(partes) < 4:
            erros.append(f"Linha {i}: esperado Tipo, DataCom, DataPagamento, Valor — recebido: {linha!r}")
            continue

        tipo_raw, data_com_raw, data_pag_raw, valor_raw = partes[0], partes[1], partes[2], partes[3]
        override_raw = partes[4] if len(partes) >= 5 else None

        tipo_key = _normalize_tipo(tipo_raw)
        if tipo_key not in _TIPO_MAP:
            erros.append(
                f"Linha {i}: tipo desconhecido {tipo_raw!r} "
                "(use Dividendo, JCP ou Rend. Tributado)"
            )
            continue

        try:
            data_com = _parse_date(data_com_raw)
            data_pag = _parse_date_or_none(data_pag_raw)
            valor = _parse_number(valor_raw)
        except ValueError as exc:
            erros.append(f"Linha {i}: {exc}")
            continue

        if data_pag is None:
            data_pag = data_com
            erros.append(
                f"Linha {i}: data de pagamento ausente ({data_pag_raw!r}); "
                f"usando data-com {data_com.strftime('%d/%m/%Y')}."
            )

        if valor < 0:
            erros.append(f"Linha {i}: valor negativo {valor}")
            continue

        override: float | None = None
        if override_raw is not None:
            try:
                ov = _parse_number(override_raw)
                # Aceita 15, 15%, 0.15 ou 0,15
                override = ov / 100.0 if ov > 1 else ov
            except ValueError as exc:
                erros.append(f"Linha {i}: alíquota inválida ({exc})")
                continue

        proventos.append(
            Provento(
                tipo=_TIPO_MAP[tipo_key],
                data_com=data_com,
                data_pagamento=data_pag,
                valor_bruto=valor,
                aliquota_override=override,
            )
        )

    return proventos, erros


# ---------------------------------------------------------------------------
# IR e agrupamento
# ---------------------------------------------------------------------------


def aplicar_ir(
    proventos: Iterable[Provento],
    criterio: YearCriterion = YearCriterion.DATA_COM,
    faixas: Sequence[AliquotaFaixa] | None = None,
    aliquota_rend_trib: float | None = None,
) -> list[ProventoLiquido]:
    faixas = list(faixas) if faixas is not None else load_aliquotas()
    if aliquota_rend_trib is None:
        aliquota_rend_trib = aliquota_rendimento_tributado()
    result: list[ProventoLiquido] = []

    for p in proventos:
        if p.aliquota_override is not None:
            aliq = p.aliquota_override
        elif p.tipo == ProventoTipo.DIVIDENDO:
            aliq = 0.0
        elif p.tipo == ProventoTipo.RENDIMENTO_TRIBUTADO:
            aliq = float(aliquota_rend_trib)
        else:
            # JCP: alíquota pela data-com (crédito)
            aliq = aliquota_jcp(p.data_com, faixas)

        ir = p.valor_bruto * aliq
        liquido = p.valor_bruto - ir
        ref = p.data_com if criterio == YearCriterion.DATA_COM else p.data_pagamento

        result.append(
            ProventoLiquido(
                tipo=p.tipo,
                data_com=p.data_com,
                data_pagamento=p.data_pagamento,
                valor_bruto=p.valor_bruto,
                aliquota=aliq,
                ir=ir,
                valor_liquido=liquido,
                ano=ref.year,
            )
        )
    return result


def agrupar_por_ano(
    liquidos: Iterable[ProventoLiquido],
    ano_corrente: int | None = None,
) -> list[AnoResumo]:
    ano_corrente = ano_corrente if ano_corrente is not None else date.today().year
    buckets: dict[int, list[ProventoLiquido]] = {}
    for item in liquidos:
        buckets.setdefault(item.ano, []).append(item)

    resumos: list[AnoResumo] = []
    for ano in sorted(buckets):
        itens = buckets[ano]
        resumos.append(
            AnoResumo(
                ano=ano,
                bruto=sum(i.valor_bruto for i in itens),
                ir=sum(i.ir for i in itens),
                liquido=sum(i.valor_liquido for i in itens),
                parcial=ano == ano_corrente,
            )
        )
    return resumos


def selecionar_anos(
    resumos: Sequence[AnoResumo],
    *,
    n_ultimos: int | None = None,
    anos_especificos: Sequence[int] | None = None,
    incluir_ano_andamento: bool = False,
    ano_corrente: int | None = None,
) -> list[AnoResumo]:
    """
    Filtra os anos que entram na base do cálculo.
    - anos_especificos tem prioridade sobre n_ultimos.
    - Ano em andamento fica de fora, salvo incluir_ano_andamento=True.
    """
    ano_corrente = ano_corrente if ano_corrente is not None else date.today().year

    if anos_especificos is not None:
        wanted = set(anos_especificos)
        selecionados = [r for r in resumos if r.ano in wanted]
        if not incluir_ano_andamento:
            selecionados = [r for r in selecionados if not (r.parcial or r.ano == ano_corrente)]
        return selecionados

    fechados = [r for r in resumos if not r.parcial and r.ano != ano_corrente]
    if n_ultimos is not None:
        fechados = fechados[-n_ultimos:]

    if incluir_ano_andamento:
        parciais = [r for r in resumos if r.parcial or r.ano == ano_corrente]
        return fechados + parciais
    return fechados


def _base_from(valores: Sequence[float], metodo: BaseMethod) -> float:
    if not valores:
        raise ValueError("nenhum ano selecionado para calcular a base")
    if metodo == BaseMethod.MEDIA:
        return float(mean(valores))
    if metodo == BaseMethod.MEDIANA:
        return float(median(valores))
    if metodo == BaseMethod.MINIMO:
        return float(min(valores))
    raise ValueError(f"método desconhecido: {metodo}")


_NOMES_MESES = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


def frequencia_meses_pagamento(
    proventos: Iterable[Provento],
    *,
    usar_data_pagamento: bool = True,
) -> list[MesFrequencia]:
    """
    Conta em quais meses os proventos caem com mais frequência.
    Por padrão usa a data de pagamento (o que o usuário pediu).
    """
    contagem: Counter[int] = Counter()
    for p in proventos:
        ref = p.data_pagamento if usar_data_pagamento else p.data_com
        contagem[ref.month] += 1

    total = sum(contagem.values())
    if total == 0:
        return []

    resultado: list[MesFrequencia] = []
    for mes, qtd in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0])):
        resultado.append(
            MesFrequencia(
                mes=mes,
                nome=_NOMES_MESES[mes],
                quantidade=qtd,
                percentual=qtd / total,
            )
        )
    return resultado


# ---------------------------------------------------------------------------
# Contas clássicas (Bazin / Graham)
# ---------------------------------------------------------------------------

BAZIN_DY = 0.06  # Décio Bazin: teto com DY mínimo de 6%


@dataclass
class ContaClassica:
    nome: str
    preco_justo: float | None
    preco_atual: float
    diferenca: float | None  # atual/justo − 1; negativo = barata
    veredito: str
    detalhe: str = ""


def preco_teto_bazin(base_liquida: float, *, dy: float = BAZIN_DY) -> float:
    """Preço teto de Bazin: provento (base) ÷ DY alvo (padrão 6%)."""
    if dy <= 0:
        raise ValueError("DY de Bazin deve ser > 0")
    return base_liquida / dy


def preco_graham(lpa: float, vpa: float) -> float:
    """
    Número de Graham clássico: √(22,5 × LPA × VPA).
    Exige LPA e VPA positivos.
    """
    if lpa <= 0 or vpa <= 0:
        raise ValueError("LPA e VPA precisam ser positivos para Graham")
    return (22.5 * lpa * vpa) ** 0.5


def _veredito_vs_justo(preco_atual: float, preco_justo: float) -> tuple[float, str]:
    diferenca = preco_atual / preco_justo - 1.0
    if diferenca < 0:
        return diferenca, f"Barata: {abs(diferenca) * 100:.1f}% abaixo"
    if diferenca > 0:
        return diferenca, f"Cara: {diferenca * 100:.1f}% acima"
    return 0.0, "No justo"


def conta_bazin(base_liquida: float, preco_atual: float, *, dy: float = BAZIN_DY) -> ContaClassica:
    justo = preco_teto_bazin(base_liquida, dy=dy)
    dif, ver = _veredito_vs_justo(preco_atual, justo)
    return ContaClassica(
        nome="Bazin",
        preco_justo=justo,
        preco_atual=preco_atual,
        diferenca=dif,
        veredito=ver,
        detalhe=f"Teto = base líquida ÷ {dy * 100:.0f}%",
    )


def conta_graham(lpa: float, vpa: float, preco_atual: float) -> ContaClassica:
    justo = preco_graham(lpa, vpa)
    dif, ver = _veredito_vs_justo(preco_atual, justo)
    return ContaClassica(
        nome="Graham",
        preco_justo=justo,
        preco_atual=preco_atual,
        diferenca=dif,
        veredito=ver,
        detalhe=f"√(22,5 × LPA {lpa:.4f} × VPA {vpa:.4f})",
    )


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


def calculate(
    texto_proventos: str,
    *,
    preco_atual: float,
    dy_desejado: float,
    ticker: str = "",
    n_ultimos: int | None = 5,
    anos_especificos: Sequence[int] | None = None,
    metodo: BaseMethod = BaseMethod.MEDIA,
    criterio: YearCriterion = YearCriterion.DATA_COM,
    incluir_ano_andamento: bool = False,
    ano_corrente: int | None = None,
    faixas: Sequence[AliquotaFaixa] | None = None,
) -> ValuationResult:
    """
    Calcula preço teto e veredito a partir do texto de proventos.

    dy_desejado: fração (ex.: 0.06 para 6%).
    diferenca = preco_atual / preco_teto - 1  (negativo = barata).
    """
    ano_corrente = ano_corrente if ano_corrente is not None else date.today().year
    proventos, erros = parse_proventos(texto_proventos)
    meses = frequencia_meses_pagamento(proventos)

    if erros and not proventos:
        return ValuationResult(
            ticker=ticker,
            preco_atual=preco_atual,
            dy_desejado=dy_desejado,
            anos_selecionados=[],
            anos_resumo=[],
            base=None,
            metodo=metodo,
            preco_teto=None,
            dy_atual=None,
            diferenca=None,
            veredito="Erro",
            aviso=None,
            erros=erros,
            meses_pagamento=meses,
        )

    liquidos = aplicar_ir(proventos, criterio=criterio, faixas=faixas)
    resumos = agrupar_por_ano(liquidos, ano_corrente=ano_corrente)
    selecionados = selecionar_anos(
        resumos,
        n_ultimos=n_ultimos,
        anos_especificos=anos_especificos,
        incluir_ano_andamento=incluir_ano_andamento,
        ano_corrente=ano_corrente,
    )

    aviso: str | None = None
    if anos_especificos is None and n_ultimos is not None and len(selecionados) < n_ultimos:
        aviso = (
            f"Histórico insuficiente: pediu {n_ultimos} anos fechados, "
            f"mas só há {len(selecionados)}."
        )

    if not selecionados:
        return ValuationResult(
            ticker=ticker,
            preco_atual=preco_atual,
            dy_desejado=dy_desejado,
            anos_selecionados=[],
            anos_resumo=resumos,
            base=None,
            metodo=metodo,
            preco_teto=None,
            dy_atual=None,
            diferenca=None,
            veredito="Sem dados",
            aviso=aviso or "Nenhum ano selecionado para o cálculo.",
            erros=erros,
            meses_pagamento=meses,
        )

    if dy_desejado <= 0:
        return ValuationResult(
            ticker=ticker,
            preco_atual=preco_atual,
            dy_desejado=dy_desejado,
            anos_selecionados=[r.ano for r in selecionados],
            anos_resumo=resumos,
            base=None,
            metodo=metodo,
            preco_teto=None,
            dy_atual=None,
            diferenca=None,
            veredito="Erro",
            aviso="DY desejado deve ser maior que zero.",
            erros=erros,
            meses_pagamento=meses,
        )

    if preco_atual <= 0:
        return ValuationResult(
            ticker=ticker,
            preco_atual=preco_atual,
            dy_desejado=dy_desejado,
            anos_selecionados=[r.ano for r in selecionados],
            anos_resumo=resumos,
            base=None,
            metodo=metodo,
            preco_teto=None,
            dy_atual=None,
            diferenca=None,
            veredito="Erro",
            aviso="Preço atual deve ser maior que zero.",
            erros=erros,
            meses_pagamento=meses,
        )

    base = _base_from([r.liquido for r in selecionados], metodo)
    preco_teto = base / dy_desejado
    dy_atual = base / preco_atual
    diferenca = preco_atual / preco_teto - 1.0

    if diferenca < 0:
        pct = abs(diferenca) * 100
        veredito = f"Barata: {pct:.1f}% abaixo do teto"
    elif diferenca > 0:
        pct = diferenca * 100
        veredito = f"Cara: {pct:.1f}% acima do teto"
    else:
        veredito = "No teto"

    return ValuationResult(
        ticker=ticker,
        preco_atual=preco_atual,
        dy_desejado=dy_desejado,
        anos_selecionados=[r.ano for r in selecionados],
        anos_resumo=resumos,
        base=base,
        metodo=metodo,
        preco_teto=preco_teto,
        dy_atual=dy_atual,
        diferenca=diferenca,
        veredito=veredito,
        aviso=aviso,
        erros=erros,
        meses_pagamento=meses,
    )
