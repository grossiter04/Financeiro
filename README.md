# Calculadora de preço teto por proventos

Calculadora local (Streamlit) para Bazin, Graham e seu DY, com **avaliação unificada** (Comprar / Esperar / Evitar + motivos), fila de status, sinal orientativo e relatório sob demanda por e-mail.

## Como rodar

```powershell
cd Financeira
uv venv .venv
uv pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
streamlit run calculadora/app.py
```

Abre em geral em `http://localhost:8501`.

## Relatório de notícias

Na página **Por setor**, use **Gerar e enviar por e-mail**. O app lê sites de investimento, analisa as ações salvas no banco e manda o relatório no seu e-mail.

### Configurar e-mail e IA

1. Copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml`.
2. Preencha SMTP (Gmail: use [senha de app](https://myaccount.google.com/apppasswords), não a senha normal).
3. (Opcional) `GEMINI_API_KEY` para análise com IA no relatório e no **Assistente**. Sem ela, o relatório sai simples e o chat fica desligado.
4. Reinicie o Streamlit.

Para reconhecer empresas pelo nome nas notícias, edite `alertas/empresas.toml` ao adicionar ações novas.

### Envio automático (GitHub Actions)

Sem depender do PC: o workflow **Relatório abertura** roda na nuvem em dias úteis às **9:45 BRT** (≈15 min antes da abertura da B3) e também pode ser disparado manualmente (**Actions → Relatório abertura → Run workflow**).

1. No GitHub do repositório: **Settings → Secrets and variables → Actions**, cadastre pelo menos `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` (e opcionalmente `SMTP_PORT`, `SMTP_FROM`, `GEMINI_API_KEY`).
2. Faça push deste repo (o job usa o `data/acoes.db` versionado). Quando salvar tickers novos na calculadora e quiser que a nuvem acompanhe, **commit + push** do `data/acoes.db`.
3. Teste localmente (usa `.streamlit/secrets.toml` se existir):

```powershell
.\.venv\Scripts\python.exe scripts\enviar_relatorio.py
# só gerar:  --dry-run
# sem Gemini: --sem-ia
```

## Assistente (chat)

Página **Assistente**: perguntas em português sobre a carteira, comparar tickers e a leitura Comprar/Esperar/Evitar. O Gemini recebe valuation, notícias, variação e um ranking `oportunidades` do catálogo de ações **salvas** (não só da carteira) — indicativo, não recomendação. Requer `GEMINI_API_KEY`.

## Como usar a calculadora

1. No topo: alterne **Calculadora**, **Por setor**, **Carteira** e **Assistente**; tema Claro/Escuro.
2. Na tela vazia, use **Buscar ticker** (salvas + catálogo) para abrir a análise. Só se o papel não estiver na lista, use o expander **Ticker fora da lista**.
3. Com o papel aberto: abas, preço/DY, contexto, **Tese**/valuation, proventos e comparativo. Para mudar de papel sem fechar a aba, use **Trocar ou abrir outro papel**.
4. No fim da página, **Configuração** (recolhido): anos, método, Selic, IPCA+, catálogo e exclusão de salvas. O botão **Ajustes** do topo só tem atalhos curtos.

## Carteira (posições)

Página **Carteira**: quantidade, preço médio, % do patrimônio e resultado vs PM.

Use **Registrar operação → Compra** (ticker, qtd, preço): se o papel já existir, o PM é recalculado na média ponderada. Venda reduz a quantidade e mantém o PM.

O Inter não tem API para app pessoal. No Super App: **Invest** → lupa → **Informe de posições** (chega por e-mail). Para carga inicial, use CSV ou ajuste manual:

```text
ticker,quantidade,preco_medio
ITUB4,100,32.50
TAEE11,200,35.40
```

Ao salvar/importar, tickers novos também entram na Calculadora (preço + proventos).

Na mesma página há uma **estimativa de proventos** (média líquida dos últimos anos fechados × quantidade, com faixa min–máx) e um gráfico de peso por setor. É referência histórica, não previsão.

## Regras de cálculo

1. **IR:** Dividendo isento; JCP 15%/17,5% pela data-com; Rend. Tributado 15% padrão.
2. Ano em andamento fora da média (salvo opção).
3. `preço teto = base ÷ DY desejado` · negativo = barata.
4. **Bazin:** `base ÷ 6%` · **Graham:** `√(22,5 × LPA × VPA)`.
5. **Avaliação unificada** (Calculadora, fila e relatório usam o mesmo motor):
   - **Qualidade:** ROE vs custo de capital, payout, FCF, dívida/EV. Qualidade fraca + preço “barato” veta Comprar (armadilha de valor).
   - **Múltiplos por setor:** bancos/seguros enfatizam P/VP+ROE; cíclicas reduzem peso do P/L; demais usam EV/EBIT e FCF yield quando disponíveis, além de Bazin/Graham.
   - **Custo de oportunidade BR:** DY estimado e earnings yield (`1÷P/L`) vs Selic e vs IPCA+ (ajustável na sidebar).
   - **Expectativas (reverse DCF):** crescimento de FCF implícito no preço; se for alto demais vs histórico, conta contra.
   - **Notícias:** só citação direta e impacto claro (±15% do score). Na Calculadora, RSS em cache de sessão.
6. **Sinal / tese:** mesmos limiares Comprar / Esperar / Evitar. Indicativo — não é recomendação de investimento.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```
