# Calculadora de preço teto por proventos

Calculadora web local + **alertas por e-mail** via GitHub Actions (grátis), para avisar quando uma ação estiver barata (Bazin / Graham / seu DY / preço manual).

## Como rodar (local)

```powershell
cd Financeira
uv venv .venv
uv pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
streamlit run calculadora/app.py
```

Abre em geral em `http://localhost:8501`.

## Alertas por e-mail (recomendado)

Checagem em **horários estratégicos** no pregão da B3 (seg–sex, fuso `America/Sao_Paulo`):

| Horário (BRT) | Motivo |
|---|---|
| **10:07** | logo após a abertura |
| **13:07** | meio do pregão (~3h depois) |
| **16:07** | ~1h antes do fechamento (~17h) |

O cron nativo do GitHub é **melhor esforço** (pode atrasar ou pular). Para disparos confiáveis, use o agendador externo abaixo. Os horários do próprio GitHub ficam como reserva.

Anti-spam: no máximo **1 e-mail por papel/critério por dia**. No dia seguinte, se ainda estiver barata, avisa de novo.

### 1. Senha de app do Gmail (exemplo)

1. Conta Google → **Segurança** → ative **Verificação em 2 etapas**.
2. Em [Senhas de app](https://myaccount.google.com/apppasswords), crie uma para “Mail”.
3. Copie a senha de 16 caracteres (não é a senha normal do Gmail).

### 2. Secrets no GitHub

No repo `grossiter04/Financeiro` → **Settings → Secrets and variables → Actions**, crie:

| Secret | Exemplo Gmail |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `seu@gmail.com` |
| `SMTP_PASSWORD` | senha de app |
| `ALERT_EMAIL_TO` | e-mail que recebe o alerta (pode ser o mesmo) |
| `SMTP_FROM` | opcional; padrão = `SMTP_USER` |

Com SMTP configurado, o e-mail tem prioridade. Telegram só entra se SMTP **não** estiver definido.

### 3. Watchlist (todas as ações salvas)

O Action vigia o que está em [`alertas/watchlist.toml`](alertas/watchlist.toml).

Para incluir **todas** as ações da calculadora local:

```powershell
python -m calculadora.sync_watchlist
git add alertas/watchlist.toml data/acoes.db
git commit -m "Atualiza watchlist com ações salvas"
git push
```

Também deixe `usar_banco_local = true` (já vem assim após o sync): no job, o `data/acoes.db` do repo entra na lista.

### 4. Testar

- Local: `python -m calculadora.alertas_cli --dry-run`
- GitHub: **Actions → Alertas de preço → Run workflow**

### 5. Despertador confiável (cron-job.org) — alertas **e** notícias

São **4 jobs** no total (não só os de preço):

| Job | Horário (BRT) | Workflow |
|---|---|---|
| Notícias | **09:37** | Relatório diário de notícias |
| Preços abertura | **10:07** | Alertas de preço |
| Preços meio | **13:07** | Alertas de preço |
| Preços pré-fechamento | **16:07** | Alertas de preço |

#### A) Token no GitHub (uma vez)

1. [Fine-grained personal access token](https://github.com/settings/personal-access-tokens/new)
2. Resource owner: sua conta · Repository access: **Only select repositories** → `Financeiro`
3. Permissions → Repository → **Actions: Read and write**
4. Generate e **copie o token** (só aparece uma vez). Não cole no chat.

#### B) Conta em [cron-job.org](https://cron-job.org)

Crie 4 jobs. Em cada um:

- **Title:** ex. `Financeiro notícias 09:37`
- **URL:**  
  - Notícias: `https://api.github.com/repos/grossiter04/Financeiro/actions/workflows/relatorio.yml/dispatches`  
  - Preços: `https://api.github.com/repos/grossiter04/Financeiro/actions/workflows/alertas.yml/dispatches`
- **Schedule:** o horário da tabela, fuso **America/Sao_Paulo**, dias **seg–sex**
- **Request method:** `POST`
- **Request headers:**
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer SEU_TOKEN_AQUI`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `Content-Type: application/json`
- **Request body:**
  - Notícias: `{"ref":"main","inputs":{"com_ia":"true"}}`
  - Preços: `{"ref":"main","inputs":{"force":"true","test_email":"false"}}`

Depois de salvar, use **“Run now”** em um job para validar (deve aparecer em Actions e o e-mail chegar).

## Relatório diário de notícias

Todo dia útil às **9h37** (antes da abertura) chega um e-mail separado com:

- **Cenário do dia**: juros, dólar, Ibovespa, política e o que isso significa para a carteira.
- **Suas ações**: preço, variação no dia e no mês, distância do teto Bazin e do preço de Graham, tendência (alta/queda/lateral) e sentimento das notícias.
- **O que esperar de cada ação**: expectativa, riscos e links das notícias relacionadas.
- **Notícias que podem mexer com a carteira**: impacto (positivo/negativo), relevância e quais ações afeta.

Fontes (RSS): InfoMoney, InvestNews, Money Times, Seu Dinheiro, Investing.com, Valor Investe, Suno e E-Investidor. A lista fica em `FEEDS`, em `calculadora/noticias.py`.

### IA (Gemini, gratuita)

O texto analítico é escrito pelo Gemini, do Google. Sem a chave, o relatório chega mesmo assim, com uma análise automática mais simples.

1. Acesse [Google AI Studio](https://aistudio.google.com/apikey) com sua conta Google.
2. Clique em **Create API key** e copie a chave.
3. No GitHub: **Settings → Secrets and variables → Actions → New repository secret**, nome `GEMINI_API_KEY`, cole a chave.
4. (Opcional) Para trocar o modelo, crie uma *variable* (aba **Variables**) `GEMINI_MODEL`, por exemplo `gemini-3.5-flash-lite`. Sem ela, tenta `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.8-flash` e `gemini-3.1-flash-lite`, nessa ordem.

### Novas ações

O relatório já usa todas as ações da watchlist. Para que ele reconheça a empresa pelo nome nas notícias (ex.: "Petrobras", não só "PETR4") e ligue notícias do setor, adicione um bloco em `alertas/empresas.toml`.

### Testar

- Local: `python -m calculadora.relatorio_cli --dry-run` (gera `alertas/relatorio.html` sem enviar)
- GitHub: **Actions → Relatório diário de notícias → Run workflow**. Uma cópia do HTML fica em *Artifacts* da execução.

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

## Hosting da UI (opcional)

A calculadora **não precisa** estar online para os alertas. Railway + volume `/app/data` se quiser UI persistente; Streamlit Cloud gratuito pode apagar o SQLite.
