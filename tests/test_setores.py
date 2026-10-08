from calculadora.setores import (
    agrupar_por_setor,
    normalizar_setor,
    rotulo_setor,
)


def test_rotulos_e_normalizacao():
    assert normalizar_setor("Bancos") == "bancos"
    assert rotulo_setor("energia") == "Energia"
    assert rotulo_setor("petroleo") == "Petróleo"
    assert rotulo_setor(None) == "Outros"


def test_agrupar_por_setor_ordem_perene():
    itens = [
        ("VALE3", "mineracao"),
        ("ITUB4", "bancos"),
        ("TAEE11", "energia"),
        ("PSSA3", "seguros"),
        ("XYZ3", "outros"),
    ]
    grupos = agrupar_por_setor(
        itens,
        setor_de=lambda x: x[1],
        chave_item=lambda x: x[0],
    )
    assert [s for s, _ in grupos] == ["bancos", "seguros", "energia", "mineracao", "outros"]
    assert [t for t, _ in grupos[0][1]] == ["ITUB4"]


def test_relatorio_html_agrupa_por_setor():
    from datetime import datetime

    from calculadora.mail import BRT
    from calculadora.noticias import Noticia
    from calculadora.relatorio import (
        MetricaAcao,
        Relatorio,
        analise_por_regras,
        renderizar_html,
        renderizar_texto,
    )

    metricas = [
        MetricaAcao("PETR4", "Petrobras", "petroleo", preco=30.0, pct_bazin=-0.1),
        MetricaAcao("ITUB4", "Itaú", "bancos", preco=40.0),
        MetricaAcao("TAEE11", "Taesa", "energia", preco=35.0, pct_bazin=-0.05),
    ]
    noticias = [
        Noticia("n1", "Petrobras sobe", "", "https://ex.com/1", "InfoMoney", None,
                acoes_diretas=["PETR4"]),
    ]
    an_not, an_acoes, resumo = analise_por_regras(metricas, noticias)
    rel = Relatorio(
        datetime(2026, 10, 7, 12, 0, tzinfo=BRT),
        metricas,
        noticias,
        an_not,
        an_acoes,
        resumo,
    )
    html = renderizar_html(rel)
    assert "Suas ações por setor" in html
    assert "Bancos" in html and "Energia" in html and "Petróleo" in html
    assert html.index("Bancos") < html.index("Petróleo")
    texto = renderizar_texto(rel)
    assert "SUAS AÇÕES POR SETOR" in texto
    assert "## Bancos" in texto
    assert "Atrativa" in html
