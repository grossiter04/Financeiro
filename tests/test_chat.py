"""Testes do assistente de chat (contexto local, sem rede)."""

from __future__ import annotations

from calculadora.chat import (
    MensagemChat,
    extrair_tickers_texto,
    montar_contexto_chat,
    montar_prompt_chat,
    pergunta_ideias_aporte,
)
from calculadora.fundamentos import Fundamentos
from calculadora.storage import AcaoSalva, PosicaoSalva

_PROV = """\
Dividendo	15/03/2025	30/03/2025	1,00
Dividendo	15/03/2024	30/03/2024	0,90
Dividendo	15/03/2023	30/03/2023	0,80
"""


def test_extrair_tickers_texto():
    assert extrair_tickers_texto("Compare ITUB4 e bbas3, ok?") == ["ITUB4", "BBAS3"]
    assert extrair_tickers_texto("sem ticker") == []
    # Raiz sem dígito → resolve contra candidatos locais (aba/salva)
    assert extrair_tickers_texto(
        "Vale a pena colocar POMO na carteira?",
        candidatos=["POMO4", "ITUB4"],
    ) == ["POMO4"]
    # Sem candidato: hipóteses ON/PN
    assert extrair_tickers_texto("e a POMO?") == ["POMO3", "POMO4"]


def test_pergunta_ideias_aporte():
    assert pergunta_ideias_aporte("Me diga ações boas para aportar")
    assert pergunta_ideias_aporte("Quais ações parecem baratas?")
    assert pergunta_ideias_aporte(
        "Analise minha carteira, quais ações estão favoráveis para comprar? Ou para venda?"
    )
    assert not pergunta_ideias_aporte("Como está minha carteira?")


def test_candidatos_reducao_na_revisao_carteira():
    pos = [
        PosicaoSalva(
            ticker="ITUB4", quantidade=100, preco_medio=30.0, updated_at="2026-01-01"
        ),
        PosicaoSalva(
            ticker="VALE3", quantidade=50, preco_medio=70.0, updated_at="2026-01-01"
        ),
    ]
    acoes = [
        AcaoSalva(
            ticker="ITUB4",
            preco=32.0,
            dy=6.0,
            proventos=_PROV,
            updated_at="2026-01-01",
        ),
        AcaoSalva(
            ticker="VALE3",
            preco=90.0,
            dy=4.0,
            proventos=_PROV,
            updated_at="2026-01-01",
        ),
    ]
    fund_ok = Fundamentos(
        ticker="ITUB4",
        lpa=3.0,
        vpa=20.0,
        fonte="teste",
        pl=10.0,
        payout=0.5,
        roe=0.20,
        fcf=1e9,
        ev=5e9,
        divida=5e8,
    )
    fund_fraco = Fundamentos(
        ticker="VALE3",
        lpa=2.0,
        vpa=40.0,
        fonte="teste",
        pl=45.0,
        payout=0.2,
        roe=0.04,
        fcf=-1e8,
        ev=3e10,
        divida=2e10,
    )
    ctx = montar_contexto_chat(
        pergunta="Analise minha carteira: comprar ou vender?",
        selic=0.15,
        ipca_mais=0.06,
        posicoes=pos,
        acoes=acoes,
        fund_cache={"ITUB4": fund_ok, "VALE3": fund_fraco},
        precos={"ITUB4": 32.0, "VALE3": 90.0},
        setores={"ITUB4": "bancos", "VALE3": "mineracao"},
    )
    assert ctx.modo == "revisao_carteira"
    assert ctx.candidatos_reducao
    assert all(r.get("ticker") for r in ctx.candidatos_reducao)
    blob = ctx.para_prompt()
    assert "candidatos_reducao" in blob
    assert "revisar/reduzir" in blob or "baixa prioridade" in blob
    # carteira enriquecida
    assert any(c.get("decisao") for c in ctx.carteira)


def test_oportunidades_prioriza_catalogo_fora_da_carteira():
    pos = [
        PosicaoSalva(
            ticker="ITUB4", quantidade=100, preco_medio=30.0, updated_at="2026-01-01"
        )
    ]
    acoes = [
        AcaoSalva(
            ticker="ITUB4",
            preco=32.0,
            dy=6.0,
            proventos=_PROV,
            updated_at="2026-01-01",
        ),
        AcaoSalva(
            ticker="VALE3",
            preco=60.0,
            dy=8.0,
            proventos=_PROV,
            updated_at="2026-01-01",
        ),
    ]
    fund = Fundamentos(
        ticker="VALE3",
        lpa=8.0,
        vpa=40.0,
        fonte="teste",
        pl=7.0,
        payout=0.5,
        roe=0.22,
        fcf=5e9,
        ev=2e10,
        divida=1e9,
    )
    ctx = montar_contexto_chat(
        pergunta="Me diga ações boas para aportar e o porque",
        selic=0.10,
        ipca_mais=0.06,
        posicoes=pos,
        acoes=acoes,
        fund_cache={"ITUB4": fund, "VALE3": fund},
        precos={"ITUB4": 32.0, "VALE3": 60.0},
        setores={"ITUB4": "bancos", "VALE3": "mineracao"},
    )
    assert ctx.modo == "ideias_aporte"
    assert ctx.oportunidades
    tickers_op = {o["ticker"] for o in ctx.oportunidades}
    assert "VALE3" in tickers_op
    vale = next(o for o in ctx.oportunidades if o["ticker"] == "VALE3")
    assert vale["na_carteira"] is False
    blob = ctx.para_prompt()
    assert "oportunidades" in blob
    assert "ações SALVAS" in blob or "catálogo" in blob.lower()


def test_montar_contexto_carteira_e_avaliacao():
    pos = [
        PosicaoSalva(
            ticker="ITUB4", quantidade=100, preco_medio=30.0, updated_at="2026-01-01"
        )
    ]
    acoes = [
        AcaoSalva(
            ticker="ITUB4",
            preco=32.0,
            dy=6.0,
            proventos=_PROV,
            updated_at="2026-01-01",
        )
    ]
    fund = Fundamentos(
        ticker="ITUB4",
        lpa=3.0,
        vpa=20.0,
        fonte="teste",
        pl=10.0,
        payout=0.5,
        roe=0.18,
        fcf=1e9,
        ev=5e9,
        divida=5e8,
    )
    ctx = montar_contexto_chat(
        pergunta="Como está ITUB4?",
        selic=0.10,
        ipca_mais=0.06,
        posicoes=pos,
        acoes=acoes,
        fund_cache={"ITUB4": fund},
        precos={"ITUB4": 32.0},
        setores={"ITUB4": "bancos"},
        noticias_por_ticker={
            "ITUB4": [
                {
                    "titulo": "Itaú reporta lucro recorde",
                    "resumo": "Resultado acima do consenso.",
                    "impacto": "positivo",
                    "fonte": "Teste",
                    "temas": "",
                    "publicado": "2026-03-01",
                    "tipo": "direta",
                }
            ]
        },
        noticias_setor=[
            {
                "titulo": "Selic estável favorece bancos",
                "resumo": "Copom mantém juros.",
                "impacto": "positivo",
                "fonte": "Teste",
                "temas": "juros e inflação",
                "tipo": "setor",
                "tickers_tema": "ITUB4",
            }
        ],
        sentimento_por_ticker={
            "ITUB4": {"sentimento": "positivo", "confiante": True, "n_diretas": 1}
        },
        mercado_por_ticker={
            "ITUB4": {
                "preco_ultimo": 32.0,
                "var_dia_pct": -1.2,
                "var_mes_pct": -4.5,
                "tendencia": "queda",
            }
        },
    )
    assert ctx.carteira
    assert ctx.carteira[0]["ticker"] == "ITUB4"
    assert any(a.get("ticker") == "ITUB4" for a in ctx.avaliacoes)
    av = next(a for a in ctx.avaliacoes if a["ticker"] == "ITUB4")
    assert av["decisao"] in ("Comprar", "Esperar", "Evitar", "Sem dados")
    assert "dy_estimado" in av
    assert av.get("sentimento_noticias") == "positivo"
    assert av.get("sentimento_confiante") is True
    assert ctx.noticias
    assert ctx.noticias[0]["titulo"].startswith("Itaú")
    assert ctx.noticias_setor
    assert ctx.mercado and ctx.mercado[0]["tendencia"] == "queda"
    assert ctx.sentimento and ctx.sentimento[0]["confiante"] is True

    blob = ctx.para_prompt()
    assert "noticias_diretas" in blob
    assert "noticias_setor" in blob
    assert "mercado" in blob
    assert "sentimento_noticias" in blob


def test_montar_prompt_inclui_contexto_e_historico():
    ctx = montar_contexto_chat(
        pergunta="carteira",
        posicoes=[],
        acoes=[],
        fund_cache={},
        precos={},
    )
    prompt = montar_prompt_chat(
        "Como está minha carteira?",
        contexto=ctx,
        historico=[MensagemChat("user", "oi"), MensagemChat("assistant", "olá")],
    )
    assert "CONTEXTO" in prompt
    assert "Como está minha carteira?" in prompt
    assert "não é recomendação" in prompt.lower() or "INDICATIVA" in prompt
    assert "Usuário: oi" in prompt
    assert "Mercado e notícias" in prompt or "notícias" in prompt.lower()
    assert "Modelo local" in prompt or "modelo local" in prompt.lower()
