"""CLI: python -m calculadora.alertas_cli [--dry-run]."""

from __future__ import annotations

import argparse
import sys

from calculadora.alertas import checar_e_avisar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checa preços e avisa por e-mail/Telegram")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só imprime; não envia aviso",
    )
    args = parser.parse_args(argv)

    result = checar_e_avisar(dry_run=args.dry_run)
    for e in result.erros:
        print(f"AVISO: {e}", file=sys.stderr)
    print(
        f"Sinais={len(result.sinais)} enviados={len(result.enviados)} "
        f"spam_ignorado={len(result.ignorados_spam)}"
    )
    if result.enviados:
        print("Enviados:", ", ".join(result.enviados))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
