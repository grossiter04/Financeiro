# Calculadora de preço teto por proventos

Calculadora web local + **alertas no Telegram** via GitHub Actions (grátis), para avisar quando uma ação estiver barata (Bazin / Graham / seu DY / preço manual).

## Como rodar (local)

```powershell
cd Financeira
uv venv .venv
uv pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
streamlit run calculadora/app.py
```

Abre em geral em `http://localhost:8501`.

## Alertas no Telegram (recomendado)

Checagem a cada **~10 minutos** no pregão da B3 (seg–sex, ~10h–18h BRT). É o intervalo mais frequente estável no GitHub Actions sem estourar minutos do repo privado. Fora do pregão não roda (preço quase não muda).

### 1. Criar o bot

1. No Telegram, abra [@BotFather](https://t.me/BotFather) → `/newbot` → copie o **token**.
2. Fale com o seu bot (Start).
3. Abra `https://api.telegram.org/bot<TOKEN>/getUpdates` e anote o **chat.id** (número).

### 2. Secrets no GitHub

No repo `grossiter04/Financeiro` → **Settings → Secrets and variables → Actions**:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 3. Watchlist

Edite [`alertas/watchlist.toml`](alertas/watchlist.toml), commit e push. Exemplo:

```toml
[[acao]]
ticker = "B3SA3"

[[acao]]
ticker = "TAEE11"
preco_maximo = 35.0
```

Critérios padrão: Bazin, Graham e seu DY (`dy_padrao`). O aviso só dispara de novo se o preço sair da zona barata e voltar (anti-spam).

### 4. Testar

- Local: `python -m calculadora.alertas_cli --dry-run` (ou sem `--dry-run` com as env vars).
- GitHub: **Actions → Alertas de preço → Run workflow**.

## Como usar a calculadora

1. Na barra lateral: anos, método e critério do ano.
2. Em cada aba: ticker (busca automática).
3. Proventos em **Opções** se quiser editar.
4. Contas Bazin/Graham, gráfico e comparativo 3/4/11.

### Formato dos proventos

```
Dividendo	14/08/2026	26/11/2026	0,08025458
JCP	14/08/2026	26/11/2026	0,11982486
```

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

A calculadora **não precisa** estar online para os alertas funcionarem. Se quiser UI na nuvem com SQLite persistente, use Railway + volume em `/app/data` (ver `Dockerfile` / `railway.toml`). O Streamlit Cloud gratuito pode apagar o banco ao hibernar.
