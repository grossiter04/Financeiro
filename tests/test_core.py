"""Testes do núcleo de cálculo."""

from __future__ import annotations

from datetime import date

import pytest

from calculadora.core import (
    AliquotaFaixa,
    BaseMethod,
    ProventoTipo,
    YearCriterion,
    aliquota_jcp,
    aplicar_ir,
    agrupar_por_ano,
    calculate,
    conta_bazin,
    conta_graham,
    parse_proventos,
    preco_graham,
    preco_teto_bazin,
    selecionar_anos,
)


EXEMPLO = """\
Dividendo	14/08/2026	26/11/2026	0,08025458
JCP	14/08/2026	26/11/2026	0,11982486
JCP	11/05/2026	26/08/2026	0,18633271
Dividendo	29/04/2026	27/05/2026	0,25336381
Dividendo	29/04/2026	27/05/2026	0,05116205
JCP	14/11/2025	28/01/2026	0,13980241
Dividendo	14/11/2025	28/01/2026	0,17298440
JCP	18/08/2025	27/11/2025	0,21299312
Dividendo	18/08/2025	27/11/2025	0,07673069
JCP	12/05/2025	27/08/2025	0,18217415
Dividendo	29/04/2025	27/11/2025	0,10730153
"""

FAIXAS = [
    AliquotaFaixa(desde=date(1900, 1, 1), aliquota=0.15),
    AliquotaFaixa(desde=date(2026, 1, 1), aliquota=0.175),
]


def test_parse_proventos_basico():
    proventos, erros = parse_proventos(EXEMPLO)
    assert erros == []
    assert len(proventos) == 11
    assert proventos[0].tipo == ProventoTipo.DIVIDENDO
    assert proventos[0].data_com == date(2026, 8, 14)
    assert proventos[0].data_pagamento == date(2026, 11, 26)
    assert proventos[0].valor_bruto == pytest.approx(0.08025458)
    assert proventos[1].tipo == ProventoTipo.JCP


def test_parse_com_ponto_e_virgula():
    texto = "JCP;11/05/2026;26/08/2026;0.18633271"
    proventos, erros = parse_proventos(texto)
    assert erros == []
    assert len(proventos) == 1
    assert proventos[0].valor_bruto == pytest.approx(0.18633271)


def test_parse_linha_invalida_reportada():
    texto = "Foo\t01/01/2025\t01/01/2025\t1,00\nJCP\t01/01/2025\t01/01/2025\t0,50"
    proventos, erros = parse_proventos(texto)
    assert len(erros) == 1
    assert "tipo desconhecido" in erros[0]
    assert len(proventos) == 1


def test_parse_override_aliquota():
    texto = "JCP\t14/11/2025\t28/01/2026\t0,13980241\t15"
    proventos, erros = parse_proventos(texto)
    assert erros == []
    assert proventos[0].aliquota_override == pytest.approx(0.15)


def test_parse_data_pagamento_ausente_usa_data_com():
    texto = "JCP\t25/03/2025\t-\t0,09455892"
    proventos, avisos = parse_proventos(texto)
    assert len(proventos) == 1
    assert proventos[0].data_com == date(2025, 3, 25)
    assert proventos[0].data_pagamento == date(2025, 3, 25)
    assert any("data de pagamento ausente" in a for a in avisos)


def test_parse_data_pagamento_vazia():
    texto = "JCP\t25/03/2025\t\t0,09455892"
    proventos, avisos = parse_proventos(texto)
    assert len(proventos) == 1
    assert proventos[0].data_pagamento == date(2025, 3, 25)
    assert any("ausente" in a for a in avisos)


def test_aliquota_jcp_por_vigencia():
    assert aliquota_jcp(date(2025, 11, 14), FAIXAS) == pytest.approx(0.15)
    assert aliquota_jcp(date(2025, 12, 31), FAIXAS) == pytest.approx(0.15)
    assert aliquota_jcp(date(2026, 1, 1), FAIXAS) == pytest.approx(0.175)
    assert aliquota_jcp(date(2026, 8, 14), FAIXAS) == pytest.approx(0.175)


def test_jcp_virada_de_ano_usa_data_com():
    """JCP com data-com 14/11/2025 pago em 28/01/2026 deve usar 15%."""
    texto = "JCP\t14/11/2025\t28/01/2026\t0,13980241"
    proventos, _ = parse_proventos(texto)
    liquidos = aplicar_ir(proventos, faixas=FAIXAS)
    assert liquidos[0].aliquota == pytest.approx(0.15)
    assert liquidos[0].ir == pytest.approx(0.13980241 * 0.15)
    assert liquidos[0].valor_liquido == pytest.approx(0.13980241 * 0.85)


def test_dividendo_isento():
    texto = "Dividendo\t14/11/2025\t28/01/2026\t0,17298440"
    proventos, _ = parse_proventos(texto)
    liquidos = aplicar_ir(proventos, faixas=FAIXAS)
    assert liquidos[0].aliquota == 0.0
    assert liquidos[0].ir == 0.0
    assert liquidos[0].valor_liquido == pytest.approx(0.17298440)


def test_jcp_2026_usa_17_5():
    texto = "JCP\t14/08/2026\t26/11/2026\t0,11982486"
    proventos, _ = parse_proventos(texto)
    liquidos = aplicar_ir(proventos, faixas=FAIXAS)
    assert liquidos[0].aliquota == pytest.approx(0.175)


def _liquido_2025_esperado() -> float:
    # Por data-com, ano 2025:
    # JCP 14/11/2025 0.13980241 * 0.85
    # Div 14/11/2025 0.17298440
    # JCP 18/08/2025 0.21299312 * 0.85
    # Div 18/08/2025 0.07673069
    # JCP 12/05/2025 0.18217415 * 0.85
    # Div 29/04/2025 0.10730153
    return (
        0.13980241 * 0.85
        + 0.17298440
        + 0.21299312 * 0.85
        + 0.07673069
        + 0.18217415 * 0.85
        + 0.10730153
    )


def test_agrupamento_2025_por_data_com():
    proventos, _ = parse_proventos(EXEMPLO)
    liquidos = aplicar_ir(proventos, criterio=YearCriterion.DATA_COM, faixas=FAIXAS)
    resumos = agrupar_por_ano(liquidos, ano_corrente=2026)
    por_ano = {r.ano: r for r in resumos}

    assert 2025 in por_ano
    assert 2026 in por_ano
    assert por_ano[2025].parcial is False
    assert por_ano[2026].parcial is True
    assert por_ano[2025].liquido == pytest.approx(_liquido_2025_esperado())


def test_ano_andamento_fora_da_media():
    proventos, _ = parse_proventos(EXEMPLO)
    liquidos = aplicar_ir(proventos, faixas=FAIXAS)
    resumos = agrupar_por_ano(liquidos, ano_corrente=2026)
    selecionados = selecionar_anos(resumos, n_ultimos=5, ano_corrente=2026)
    assert all(r.ano != 2026 for r in selecionados)
    assert [r.ano for r in selecionados] == [2025]


def test_incluir_ano_andamento():
    proventos, _ = parse_proventos(EXEMPLO)
    liquidos = aplicar_ir(proventos, faixas=FAIXAS)
    resumos = agrupar_por_ano(liquidos, ano_corrente=2026)
    selecionados = selecionar_anos(
        resumos, n_ultimos=5, incluir_ano_andamento=True, ano_corrente=2026
    )
    assert [r.ano for r in selecionados] == [2025, 2026]


def test_calculate_3_vs_5_anos_aviso():
    # Só há 1 ano fechado (2025); pedido de 5 gera aviso
    result = calculate(
        EXEMPLO,
        preco_atual=10.0,
        dy_desejado=0.06,
        ticker="TEST3",
        n_ultimos=5,
        ano_corrente=2026,
        faixas=FAIXAS,
    )
    assert result.aviso is not None
    assert "5" in result.aviso
    assert result.base == pytest.approx(_liquido_2025_esperado())
    assert result.preco_teto == pytest.approx(_liquido_2025_esperado() / 0.06)

    result3 = calculate(
        EXEMPLO,
        preco_atual=10.0,
        dy_desejado=0.06,
        ticker="TEST3",
        n_ultimos=3,
        ano_corrente=2026,
        faixas=FAIXAS,
    )
    # Mesma base (só 1 ano disponível), mas aviso menciona 3
    assert result3.base == pytest.approx(result.base)
    assert result3.aviso is not None
    assert "3" in result3.aviso


def test_veredito_barata_e_cara():
    liquido = _liquido_2025_esperado()
    teto = liquido / 0.06

    barata = calculate(
        EXEMPLO,
        preco_atual=teto * 0.8,
        dy_desejado=0.06,
        n_ultimos=1,
        ano_corrente=2026,
        faixas=FAIXAS,
    )
    assert "Barata" in barata.veredito
    assert barata.diferenca == pytest.approx(-0.2)

    cara = calculate(
        EXEMPLO,
        preco_atual=teto * 1.1,
        dy_desejado=0.06,
        n_ultimos=1,
        ano_corrente=2026,
        faixas=FAIXAS,
    )
    assert "Cara" in cara.veredito
    assert cara.diferenca == pytest.approx(0.1)


def test_metodos_media_mediana_minimo():
    # Fabricar 3 anos fechados
    texto = """\
Dividendo	01/06/2023	01/07/2023	1,00
Dividendo	01/06/2024	01/07/2024	2,00
Dividendo	01/06/2025	01/07/2025	3,00
"""
    media = calculate(
        texto, preco_atual=50, dy_desejado=0.1, n_ultimos=3, metodo=BaseMethod.MEDIA, ano_corrente=2026
    )
    mediana = calculate(
        texto, preco_atual=50, dy_desejado=0.1, n_ultimos=3, metodo=BaseMethod.MEDIANA, ano_corrente=2026
    )
    minimo = calculate(
        texto, preco_atual=50, dy_desejado=0.1, n_ultimos=3, metodo=BaseMethod.MINIMO, ano_corrente=2026
    )
    assert media.base == pytest.approx(2.0)
    assert mediana.base == pytest.approx(2.0)
    assert minimo.base == pytest.approx(1.0)


def test_rendimento_tributado_com_ir():
    texto = "Rend. Tributado\t26/04/2024\t08/05/2024\t0,01575862"
    proventos, erros = parse_proventos(texto)
    assert erros == []
    assert proventos[0].tipo == ProventoTipo.RENDIMENTO_TRIBUTADO
    liquidos = aplicar_ir(proventos, faixas=FAIXAS, aliquota_rend_trib=0.15)
    assert liquidos[0].aliquota == pytest.approx(0.15)
    assert liquidos[0].valor_liquido == pytest.approx(0.01575862 * 0.85)


def test_rendimento_tributado_override_20():
    texto = "Rendimento Tributado\t26/04/2024\t08/05/2024\t0,01575862\t20"
    proventos, erros = parse_proventos(texto)
    assert erros == []
    liquidos = aplicar_ir(proventos, faixas=FAIXAS)
    assert liquidos[0].aliquota == pytest.approx(0.20)


def test_frequencia_meses_pagamento():
    from calculadora.core import frequencia_meses_pagamento

    texto = """\
JCP	25/03/2025	-\t0,09455892
JCP	23/12/2024	30/12/2025	0,09790376
JCP	23/09/2024	30/06/2025	0,08260111
Dividendo	23/08/2024	30/08/2024	0,49635976
JCP	21/06/2024	30/06/2025	0,07510604
Dividendo	29/04/2024	27/12/2024	0,12113430
JCP	26/03/2024	30/12/2025	0,08778293
"""
    proventos, _ = parse_proventos(texto)
    meses = frequencia_meses_pagamento(proventos)
    # Por data de pagamento: jun=2, dez=3, ago=1, mar=1 (março via fallback do -)
    por_nome = {m.nome: m.quantidade for m in meses}
    assert por_nome["Dezembro"] == 3
    assert por_nome["Junho"] == 2
    assert meses[0].nome == "Dezembro"


def test_anos_especificos():
    texto = """\
Dividendo	01/06/2022	01/07/2022	1,00
Dividendo	01/06/2023	01/07/2023	10,00
Dividendo	01/06/2024	01/07/2024	2,00
Dividendo	01/06/2025	01/07/2025	3,00
"""
    result = calculate(
        texto,
        preco_atual=50,
        dy_desejado=0.1,
        anos_especificos=[2022, 2024, 2025],
        n_ultimos=None,
        metodo=BaseMethod.MEDIA,
        ano_corrente=2026,
    )
    assert result.anos_selecionados == [2022, 2024, 2025]
    assert result.base == pytest.approx((1 + 2 + 3) / 3)


def test_bazin_teto():
    assert preco_teto_bazin(1.2) == pytest.approx(20.0)
    conta = conta_bazin(1.2, 18.0)
    assert conta.preco_justo == pytest.approx(20.0)
    assert conta.diferenca is not None and conta.diferenca < 0
    assert "Barata" in conta.veredito


def test_graham_numero():
    # √(22.5 × 4 × 20) = √1800 ≈ 42.426
    justo = preco_graham(4.0, 20.0)
    assert justo == pytest.approx((22.5 * 4 * 20) ** 0.5)
    conta = conta_graham(4.0, 20.0, 40.0)
    assert conta.diferenca is not None and conta.diferenca < 0


def test_graham_lpa_negativo():
    with pytest.raises(ValueError):
        preco_graham(-1.0, 10.0)
