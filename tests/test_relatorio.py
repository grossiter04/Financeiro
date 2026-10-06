from datetime import datetime

from calculadora.alertas import _BRT
from calculadora.noticias import Noticia
from calculadora.relatorio import (
    MetricaAcao,
    Relatorio,
    _aplicar_ia,
    _extrair_json,
    analise_por_regras,
    renderizar_html,
    renderizar_texto,
)


def _dados():
    metricas = [
        MetricaAcao("PETR4", "Petrobras", "petroleo", preco=30.0, var_dia=0.03, var_mes=0.08, pct_bazin=-0.1),
        MetricaAcao("ITUB4", "Itaú", "bancos", preco=40.0, var_dia=-0.01, var_mes=-0.05),
    ]
    noticias = [
        Noticia("n1", "Petrobras dispara com lucro recorde", "", "https://ex.com/1", "InfoMoney", None,
                acoes_diretas=["PETR4"]),
        Noticia("n2", "Copom mantém Selic", "", "https://ex.com/2", "Suno", None,
                temas=["juros e inflação"], acoes_tema=["ITUB4"]),
    ]
    return metricas, noticias


def test_regras_classifica_impacto_e_tendencia():
    metricas, noticias = _dados()
    an_not, an_acoes, resumo = analise_por_regras(metricas, noticias)
    assert an_not["n1"].impacto == "positivo"
    assert an_acoes["PETR4"].tendencia == "alta"
    assert an_acoes["PETR4"].sentimento == "positivo"
    assert an_acoes["ITUB4"].tendencia == "queda"
    assert an_acoes["ITUB4"].noticias == ["n2"]
    assert "sem IA" in resumo


def test_aplicar_ia_filtra_ids_e_tickers_desconhecidos():
    metricas, noticias = _dados()
    an_not, an_acoes, _ = analise_por_regras(metricas, noticias)
    dados = {
        "resumo_mercado": "Mercado otimista.",
        "destaques": ["Selic estável"],
        "noticias": [
            {"id": "n1", "impacto": "positivo", "relevancia": 9, "acoes": ["PETR4", "XXXX3"], "explicacao": "Lucro."},
            {"id": "inventada", "impacto": "negativo"},
        ],
        "acoes": [{"ticker": "PETR4", "tendencia": "alta", "sentimento": "positivo", "expectativa": "Boa.", "noticias": ["n1", "zz"]}],
    }
    resumo, destaques = _aplicar_ia(dados, metricas, noticias, an_not, an_acoes)
    assert resumo == "Mercado otimista."
    assert destaques == ["Selic estável"]
    assert set(an_not) == {"n1"}
    assert an_not["n1"].relevancia == 5
    assert an_not["n1"].acoes == ["PETR4"]
    assert an_acoes["PETR4"].noticias == ["n1"]
    assert an_acoes["ITUB4"].noticias == ["n2"]


def test_extrair_json_com_cercas():
    assert _extrair_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_renderiza_html_e_texto():
    metricas, noticias = _dados()
    an_not, an_acoes, resumo = analise_por_regras(metricas, noticias)
    rel = Relatorio(datetime(2026, 10, 6, 9, 37, tzinfo=_BRT), metricas, noticias, an_not, an_acoes, resumo)
    html = renderizar_html(rel)
    assert "PETR4" in html and "https://ex.com/1" in html and "<table" in html
    texto = renderizar_texto(rel)
    assert "PETR4" in texto and "Copom mantém Selic" in texto
