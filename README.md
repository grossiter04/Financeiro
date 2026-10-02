# Calculadora de preço teto por proventos

Calculadora web local e direta: você cola os proventos de uma ou mais ações, informa o preço atual e o DY desejado, e ela diz se cada ação está **barata ou cara** (e em quantos %).

Sem cadastro de carteira, sem banco de dados e sem gráficos.

## Como rodar

Com [uv](https://github.com/astral-sh/uv) (recomendado no Windows):

```powershell
cd Financeira
uv venv .venv
uv pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
streamlit run calculadora/app.py
```

Ou com Python/pip, se já estiver instalado:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run calculadora/app.py
```

Abre no navegador (em geral `http://localhost:8501`).

## Como usar

1. Na barra lateral, escolha:
  - **Anos:** últimos N anos fechados (ex.: 3 ou 5) **ou** anos específicos (ex.: `2021,2023,2024`).
  - **Método:** média, mediana ou menor ano (pessimista).
  - **Critério do ano:** data-com (padrão) ou data de pagamento.
  - Se o ano em andamento deve entrar no cálculo (desligado por padrão).
2. Em cada aba de ação: ticker, preço atual, DY desejado (%) e cole os proventos.
3. Clique em **Salvar / atualizar** para gravar no SQLite local (`data/acoes.db`). Na próxima vez, a ação aparece no seletor **Abrir** da barra lateral.
4. **Atualizar preço** busca a cotação quase em tempo real (Yahoo Finance / B3). Em pregão o atraso costuma ser baixo; fora do horário, mostra o último negócio.
5. Para atualizar proventos: edite o texto e salve de novo (mesmo ticker sobrescreve).
6. O resultado aparece na hora. Com 2+ abas, há uma tabela comparativa.

### Formato dos proventos

```
Dividendo	14/08/2026	26/11/2026	0,08025458
JCP	14/08/2026	26/11/2026	0,11982486
```

- Separador: tab ou `;`
- Datas: `dd/mm/aaaa`
- Decimal com vírgula ou ponto
- Tipos: `Dividendo`, `JCP`, `Rend. Tributado` (também `Rendimento Tributado`)
- Coluna opcional 5: alíquota forçada (ex.: `15`, `20` ou `0,15`)

Há um botão **Colar exemplo** na tela com dados de demonstração.

## Regras de cálculo

1. **IR:**
  - Dividendo: isento
  - JCP (pela data-com): 15% até 2025 / 17,5% a partir de 2026
  - Rend. Tributado: 15% por padrão (em `config.toml`; FIIs costumam ser 20% — force na 5ª coluna)
2. Ano em andamento fica **fora** da média (salvo se você marcar a opção).
3. `base` = média / mediana / mínimo do líquido nos anos escolhidos
  `preço teto = base ÷ DY desejado`  
   `% vs teto = preço atual ÷ preço teto − 1` (negativo = barata)

As alíquotas ficam em `[config.toml](config.toml)`.

## OkaneBox (opcional)

Em cada aba há **Buscar na OkaneBox**:

- **Proventos** e **última cotação** exigem plano premium. Na barra lateral, informe o **mesmo e-mail** da compra (a API usa `Authorization: Bearer seu@email.com`).
- **Sem token**, a ferramenta só tenta preço histórico gratuito (atraso ~15 dias) e pede para colar os proventos manualmente — que continua sendo o caminho principal.

Documentação: [API de proventos](https://www.okanebox.com.br/como-usar/api-dividendos-proventos/).

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

=======

# Financeiro

> > > > > > > 494a59daa4e5394f077489011dabc0aa54c2bee8

