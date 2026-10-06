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

O GitHub às vezes **atrasa** o cron em alguns minutos. Se não aparecer na lista, rode **Run workflow** (deixe **force** ligado para garantir e-mail).

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
