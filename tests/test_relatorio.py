from datetime import datetime

from calculadora.mail import BRT
from calculadora.noticias import Noticia
from calculadora.relatorio import (
    MetricaAcao,
    Relatorio,
    _aplicar_ia,
    _extrair_json,
    analise_por_regras,
    renderizar_html,
    renderizar_texto,
    sentimento_de_diretas,
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
    assert an_not["n1"].acoes == ["PETR4"]  # só direta
    assert an_not["n2"].acoes == []  # setor não entra em acoes
    assert an_acoes["PETR4"].tendencia == "alta"
    assert an_acoes["PETR4"].sentimento == "positivo"
    assert an_acoes["PETR4"].sentimento_confiante is True
    assert an_acoes["PETR4"].noticias_diretas == ["n1"]
    assert an_acoes["ITUB4"].tendencia == "queda"
    assert an_acoes["ITUB4"].sentimento == "neutro"
    assert an_acoes["ITUB4"].sentimento_confiante is False
    assert an_acoes["ITUB4"].noticias == ["n2"]  # contexto de setor
    assert an_acoes["ITUB4"].noticias_diretas == []
    assert "rígida" in resumo or "rigida" in resumo.lower() or "citam" in resumo
    assert an_acoes["PETR4"].sinal is not None
    assert an_acoes["PETR4"].sinal.codigo == "compra"
    # ITUB4 sem valuation + sem notícia direta → sem_dados
    assert an_acoes["ITUB4"].sinal is not None
    assert an_acoes["ITUB4"].sinal.codigo == "sem_dados"
    assert an_acoes["ITUB4"].sentimento_confiante is False


def test_sentimento_de_diretas_exige_citacao():
    from calculadora.relatorio import AnaliseNoticia

    _, noticias = _dados()
    an_not = {
        "n1": AnaliseNoticia("n1", impacto="positivo", acoes=["PETR4"]),
        "n2": AnaliseNoticia("n2", impacto="negativo", acoes=[]),
    }
    sent, conf, ids = sentimento_de_diretas("PETR4", noticias, an_not)
    assert sent == "positivo" and conf and ids == ["n1"]
    sent2, conf2, ids2 = sentimento_de_diretas("ITUB4", noticias, an_not)
    assert sent2 == "neutro" and not conf2 and ids2 == []


def test_aplicar_ia_filtra_setor_e_recalcula_sentimento():
    metricas, noticias = _dados()
    an_not, an_acoes, _ = analise_por_regras(metricas, noticias)
    dados = {
        "resumo_mercado": "Mercado otimista.",
        "destaques": ["Selic estável"],
        "noticias": [
            # IA tenta ligar Selic a ITUB4 — acoes ficam [] (setor), mas a notícia permanece
            {"id": "n2", "impacto": "negativo", "relevancia": 5, "acoes": ["ITUB4"], "explicacao": "Juros."},
            {"id": "n1", "impacto": "positivo", "relevancia": 9, "acoes": ["PETR4", "XXXX3"], "explicacao": "Lucro."},
            {"id": "inventada", "impacto": "negativo"},
        ],
        "acoes": [
            {
                "ticker": "PETR4",
                "tendencia": "alta",
                "sentimento": "negativo",  # IA mentirosa — deve ser sobrescrito
                "expectativa": "Boa.",
                "noticias": ["n1", "zz"],
            },
            {
                "ticker": "ITUB4",
                "tendencia": "queda",
                "sentimento": "negativo",  # sem citação direta — neutro
                "expectativa": "Pressão de juros.",
                "noticias": ["n2"],
            },
        ],
    }
    resumo, destaques = _aplicar_ia(dados, metricas, noticias, an_not, an_acoes)
    assert resumo == "Mercado otimista."
    assert destaques == ["Selic estável"]
    assert an_not["n1"].relevancia == 5
    assert an_not["n1"].acoes == ["PETR4"]
    assert an_not["n2"].acoes == []  # setor não vira vínculo de sentimento
    assert an_not["n2"].explicacao == "Juros."
    # Notícias das regras não sumiram só porque a IA omitiu alguma
    assert set(an_not) >= {"n1", "n2"}
    # Sentimento recalculado: PETR4 positivo; ITUB4 neutro sem confiança
    assert an_acoes["PETR4"].sentimento == "positivo"
    assert an_acoes["PETR4"].sentimento_confiante is True
    assert an_acoes["PETR4"].noticias_diretas == ["n1"]
    assert an_acoes["ITUB4"].sentimento == "neutro"
    assert an_acoes["ITUB4"].sentimento_confiante is False
    assert an_acoes["ITUB4"].expectativa == "Pressão de juros."


def test_aplicar_ia_nao_apaga_noticias_omitidas_pela_ia():
    metricas, noticias = _dados()
    an_not, an_acoes, _ = analise_por_regras(metricas, noticias)
    # IA só devolve n1 — n2 (setor) e qualquer outra das regras devem permanecer
    dados = {
        "resumo_mercado": "Ok.",
        "destaques": [],
        "noticias": [
            {"id": "n1", "impacto": "positivo", "relevancia": 4, "acoes": ["PETR4"], "explicacao": "Lucro."},
        ],
        "acoes": [],
    }
    _aplicar_ia(dados, metricas, noticias, an_not, an_acoes)
    assert "n1" in an_not and "n2" in an_not
    html = renderizar_html(
        Relatorio(datetime(2026, 10, 6, 9, 37, tzinfo=BRT), metricas, noticias, an_not, an_acoes, "Ok.")
    )
    assert "Notícias que podem mexer com a carteira" in html
    assert "Petrobras dispara" in html
    assert "Copom mantém Selic" in html


def test_extrair_json_com_cercas():
    assert _extrair_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extrair_json_com_virgula_final():
    # Resposta típica da IA com trailing comma
    bruto = '{"resumo_mercado": "ok", "destaques": ["a",], "noticias": [],}'
    assert _extrair_json(bruto)["resumo_mercado"] == "ok"
    assert _extrair_json(bruto)["destaques"] == ["a"]


def test_renderiza_html_e_texto():
    metricas, noticias = _dados()
    an_not, an_acoes, resumo = analise_por_regras(metricas, noticias)
    rel = Relatorio(datetime(2026, 10, 6, 9, 37, tzinfo=BRT), metricas, noticias, an_not, an_acoes, resumo)
    html = renderizar_html(rel)
    assert "PETR4" in html and "https://ex.com/1" in html and "<table" in html
    assert "Próx. com" in html and "Últ. com" in html
    assert "Atrativa" in html
    assert "citação direta" in html or "citações diretas" in html
    texto = renderizar_texto(rel)
    assert "PETR4" in texto and "Copom mantém Selic" in texto
    assert "próx. com" in texto and "últ. com" in texto
    assert "Atrativa" in texto
    assert "neutro*" in texto  # ITUB4 sem evidência direta
