# Calculadora de preço teto por proventos

Calculadora web: cola (ou busca) os proventos de uma ou mais ações, informa o preço atual e o DY desejado, e ela diz se cada ação está **barata ou cara** (e em quantos %). Inclui contas Bazin/Graham, comparativo ON/PN/Unit e gráfico de preço.

## Como rodar (local)

Com [uv](https://github.com/astral-sh/uv) (recomendado no Windows):

```powershell
cd Financeira
uv venv .venv
uv pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
streamlit run calculadora/app.py
```

Ou com Python/pip:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run calculadora/app.py
```

Abre no navegador (em geral `http://localhost:8501`).

## Deploy (Streamlit Community Cloud)

O caminho mais simples para acessar de fora:

1. Abra [share.streamlit.io](https://share.streamlit.io) e entre com a conta GitHub (`grossiter04`).
2. Autorize o Streamlit a ler repositórios **privados** (o repo `Financeiro` é privado).
3. **Create app** → **Yup, I have an app**.
4. Preencha:
   - **Repository:** `grossiter04/Financeiro`
   - **Branch:** `main`
   - **Main file path:** `calculadora/app.py`
   - **Python version** (Advanced): `3.12`
5. Clique em **Deploy**.

A URL fica em `https://….streamlit.app`. Cada `git push` na `main` atualiza o app.

### Limitações no Cloud (importante)

- O SQLite (`data/acoes.db`) **não é permanente**: o disco pode ser recriado quando o app hiberna ou reinicia. Ações salvas podem sumir.
- O plano gratuito **hiberna** após inatividade; a primeira abertura depois disso demora um pouco.
- Alertas WhatsApp/e-mail **ainda não** rodam no Cloud só com o Streamlit — isso precisa de um worker agendado (próximo passo).

## Como usar

1. Na barra lateral, escolha anos, método (média/mediana/mínimo) e critério do ano.
2. Em cada aba: ticker (busca automática de preço, proventos e descrição).
3. Proventos ficam em **Opções** caso queira editar manualmente.
4. Veja contas clássicas (Bazin/Graham), gráfico e comparativo de classes (3/4/11).

### Formato dos proventos

```
Dividendo	14/08/2026	26/11/2026	0,08025458
JCP	14/08/2026	26/11/2026	0,11982486
```

- Separador: tab ou `;`
- Datas: `dd/mm/aaaa`
- Decimal com vírgula ou ponto
- Tipos: `Dividendo`, `JCP`, `Rend. Tributado`
- Coluna opcional 5: alíquota forçada (ex.: `15`, `20` ou `0,15`)

## Regras de cálculo

1. **IR:**
   - Dividendo: isento
   - JCP (pela data-com): 15% até 2025 / 17,5% a partir de 2026
   - Rend. Tributado: 15% por padrão (em `config.toml`)
2. Ano em andamento fica **fora** da média (salvo se marcar a opção).
3. `base` = média / mediana / mínimo do líquido nos anos escolhidos  
   `preço teto = base ÷ DY desejado`  
   `% vs teto = preço atual ÷ preço teto − 1` (negativo = barata)
4. **Bazin:** `base ÷ 6%`  
   **Graham:** `√(22,5 × LPA × VPA)`

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```
