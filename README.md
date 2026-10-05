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

## Deploy (Railway — dados persistentes)

O Streamlit Community Cloud **não** guarda o SQLite com segurança (disco efêmero). Use o **Railway** com volume:

1. Conta em [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub** → `grossiter04/Financeiro` (branch `main`).
2. O serviço sobe via `Dockerfile` + `railway.toml`.
3. No serviço, adicione um **Volume** com mount path **`/app/data`**.
4. Variáveis (Settings → Variables):
   - `DATA_DIR=/app/data` (já é o default da imagem; confirme se quiser)
5. **Settings → Networking → Generate Domain** para obter a URL pública.
6. Teste: salve uma ação → **Restart** o serviço → confira se a ação ainda aparece em **Abrir**.

Cada `git push` na `main` redeploya. O volume `/app/data` **não** é apagado no restart.

Pode apagar o app antigo em `*.streamlit.app` no [share.streamlit.io](https://share.streamlit.io) — ele não é mais necessário.

### Alertas (próximo passo)

Com o SQLite no volume, dá para rodar depois um worker/cron no mesmo projeto Railway para avisar por e-mail/WhatsApp quando o preço cruzar Bazin/Graham/teto.

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
