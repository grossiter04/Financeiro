"""Gera alertas/watchlist.toml a partir de data/acoes.db."""

from __future__ import annotations

import argparse
from pathlib import Path

from calculadora.storage import listar_acoes, resolve_db_path

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = _ROOT / "alertas" / "watchlist.toml"

_HEADER = """# Lista de ações para vigiar (GitHub Actions → e-mail).
# Gerada a partir do banco local. Para atualizar:
#   python -m calculadora.sync_watchlist
# Depois: git add / commit / push.

dy_padrao = 6.0
n_anos = 5
avisar_bazin = true
avisar_graham = true
avisar_dy = true

# true = também inclui o que estiver em data/acoes.db no momento do job
usar_banco_local = true

"""


def sync_watchlist(*, out: Path | None = None, db_path: Path | None = None) -> list[str]:
    acoes = listar_acoes(db_path or resolve_db_path())
    tickers = sorted({a.ticker.strip().upper() for a in acoes if a.ticker.strip()})
    destino = out or DEFAULT_OUT
    destino.parent.mkdir(parents=True, exist_ok=True)

    linhas = [_HEADER.rstrip(), ""]
    if not tickers:
        linhas.append("# (banco vazio — adicione ações na calculadora e rode de novo)")
    for t in tickers:
        # DY salvo na calculadora, se houver
        dy = next((a.dy for a in acoes if a.ticker.upper() == t), 6.0)
        linhas.append("[[acao]]")
        linhas.append(f'ticker = "{t}"')
        if abs(float(dy) - 6.0) > 1e-9:
            linhas.append(f"dy_desejado = {float(dy):.2f}")
        linhas.append("")

    destino.write_text("\n".join(linhas).rstrip() + "\n", encoding="utf-8")
    return tickers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sincroniza watchlist.toml com o SQLite")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Caminho do watchlist.toml",
    )
    args = parser.parse_args(argv)
    tickers = sync_watchlist(out=args.out)
    print(f"{len(tickers)} ticker(s) -> {args.out}")
    if tickers:
        print(", ".join(tickers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
