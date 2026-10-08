from calculadora.sinal import calcular_sinal, formatar_margem_pct, item_fila_de_salva


def test_sinal_barata_atrativa():
    s = calcular_sinal(pct_bazin=-0.20, pct_graham=-0.18)
    assert s.codigo == "compra"
    assert s.rotulo == "Atrativa"
    assert "Bazin" in s.resumo


def test_sinal_muito_cara_esticada():
    s = calcular_sinal(pct_bazin=0.25, pct_graham=0.30)
    assert s.codigo == "cautela"
    assert s.rotulo == "Esticada"


def test_sinal_levemente_cara_fica_neutra():
    # Antes virava cautela fácil; agora margem moderada fica neutra
    s = calcular_sinal(pct_bazin=0.08, pct_graham=0.06)
    assert s.codigo == "aguardar"
    assert s.rotulo == "Neutra"


def test_sinal_ignora_sentimento_sem_flag():
    s = calcular_sinal(pct_bazin=0.0, sentimento="negativo", usar_sentimento=False)
    assert s.codigo == "aguardar"
    assert s.score == 0.0


def test_sinal_margem_absurda_nao_entra_no_score():
    s = calcular_sinal(pct_bazin=42.0)  # +4200% — ignorada no score
    assert s.codigo == "sem_dados"
    assert s.score == 0.0


def test_formatar_margem_absurda():
    assert formatar_margem_pct(42.0) == "muito acima"
    assert formatar_margem_pct(-5.0) == "muito abaixo"
    assert formatar_margem_pct(-0.12) == "-12%"


def test_sinal_noticias_reforcam_atrativa():
    s = calcular_sinal(
        pct_bazin=-0.16,
        pct_graham=-0.16,
        sentimento="positivo",
        usar_sentimento=True,
    )
    assert s.codigo == "compra"


def test_sinal_sem_dados():
    s = calcular_sinal()
    assert s.codigo == "sem_dados"


def test_item_fila_com_proventos():
    texto = (
        "Dividendo\t15/05/2024\t15/06/2024\t1,20\n"
        "Dividendo\t15/05/2023\t15/06/2023\t1,20\n"
        "Dividendo\t15/05/2022\t15/06/2022\t1,20\n"
        "Dividendo\t15/05/2021\t15/06/2021\t1,20\n"
        "Dividendo\t15/05/2020\t15/06/2020\t1,20\n"
    )
    item = item_fila_de_salva(
        ticker="TEST3",
        preco=15.0,
        dy=6.0,
        proventos=texto,
    )
    assert item.ticker == "TEST3"
    assert item.pct_bazin is not None and item.pct_bazin < 0
    assert item.sinal.codigo == "compra"
    assert "TEST3" in item.texto
