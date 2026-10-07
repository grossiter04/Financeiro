# Calculadora de preço teto por proventos

Calculadora local (Streamlit) para Bazin, Graham e seu DY, com relatório de notícias sob demanda por e-mail.

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

Na **barra lateral**, use **Gerar e enviar por e-mail**. O app lê sites de investimento, analisa as ações salvas no banco e manda o relatório no seu e-mail.

### Configurar e-mail e IA

1. Copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml`.
2. Preencha SMTP (Gmail: use [senha de app](https://myaccount.google.com/apppasswords), não a senha normal).
3. (Opcional) `GEMINI_API_KEY` para análise com IA. Sem ela, sai análise simples.
4. Reinicie o Streamlit.

Para reconhecer empresas pelo nome nas notícias, edite `alertas/empresas.toml` ao adicionar ações novas.

## Como usar a calculadora

1. Na barra lateral: anos, método e critério do ano.
2. Em cada aba: ticker (busca automática).
3. Proventos em **Opções** se quiser editar.
4. Contas Bazin/Graham, gráfico e comparativo 3/4/11.

## Regras de cálculo

1. **IR:** Dividendo isento; JCP 15%/17,5% pela data-com; Rend. Tributado 15% padrão.
2. Ano em andamento fora da média (salvo opção).
3. `preço teto = base ÷ DY desejado` · negativo = barata.
4. **Bazin:** `base ÷ 6%` · **Graham:** `√(22,5 × LPA × VPA)`.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```
