from calculadora.noticias import Empresa, Noticia, ligar_noticias, parse_rss, raiz_ticker

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>X</title>
<item><title>Petrobras sobe com petróleo</title><link>https://ex.com/a</link>
<description><![CDATA[<p>A estatal &amp; o Brent</p>]]></description>
<pubDate>Tue, 06 Oct 2026 08:00:00 -0300</pubDate></item>
<item><title>Sem link</title></item>
</channel></rss>"""

EMPRESAS = {
    "PETR": Empresa("PETR", nomes=["Petrobras"], setor="petroleo", estatal=True),
    "VALE": Empresa("VALE", nomes_exatos=["Vale"], setor="mineracao", ignorar=["Vale lembrar"]),
    "ITUB": Empresa("ITUB", nomes=["Itaú"], setor="bancos"),
}
TICKERS = ["PETR3", "PETR4", "VALE3", "ITUB4"]


def _n(titulo: str, resumo: str = "") -> Noticia:
    return Noticia(id="x", titulo=titulo, resumo=resumo, link="l", fonte="f", publicado=None)


def test_parse_rss_limpa_html_e_ignora_sem_link():
    itens = parse_rss(RSS, "Fonte")
    assert len(itens) == 1
    assert itens[0].titulo == "Petrobras sobe com petróleo"
    assert itens[0].resumo == "A estatal & o Brent"
    assert itens[0].publicado is not None


def test_parse_rss_invalido_retorna_vazio():
    assert parse_rss("<html>nada", "F") == []


def test_raiz_ticker():
    assert raiz_ticker("SANB11") == "SANB"
    assert raiz_ticker("petr4.sa") == "PETR"


def test_liga_por_nome_ticker_e_tema():
    ns = [
        _n("Petrobras anuncia dividendos"),
        _n("ITUB4 dispara 5%"),
        _n("Copom corta a Selic"),
    ]
    ligar_noticias(ns, TICKERS, EMPRESAS)
    assert ns[0].acoes_diretas == ["PETR3", "PETR4"]
    assert ns[1].acoes_diretas == ["ITUB4"]
    assert ns[2].acoes_diretas == []
    assert "juros e inflação" in ns[2].temas
    assert ns[2].acoes_tema == ["ITUB4"]


def test_nome_exato_e_frases_ignoradas():
    ns = [_n("Vale lembrar que o mercado abriu"), _n("vale a pena investir?"), _n("Vale tem lucro recorde")]
    ligar_noticias(ns, TICKERS, EMPRESAS)
    assert ns[0].acoes_diretas == []
    assert ns[1].acoes_diretas == []
    assert ns[2].acoes_diretas == ["VALE3"]


def test_tema_eleicao_pega_estatais():
    ns = [_n("Lula e Flávio Bolsonaro vão ao 2º turno")]
    ligar_noticias(ns, TICKERS, EMPRESAS)
    assert "eleição e governo" in ns[0].temas
    assert ns[0].acoes_tema == ["PETR3", "PETR4"]
