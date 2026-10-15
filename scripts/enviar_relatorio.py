#!/usr/bin/env python3
"""Gera o relatório de notícias e envia por e-mail (CLI / GitHub Actions)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _carregar_secrets_locais() -> None:
    """Se existir .streamlit/secrets.toml, espelha para o ambiente (só chaves vazias)."""
    path = ROOT / ".streamlit" / "secrets.toml"
    if not path.is_file():
        return
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    from calculadora.mail import apply_runtime_secrets

    apply_runtime_secrets(tomllib.loads(path.read_bytes()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gera o relatório das ações salvas e envia por e-mail.",
    )
    parser.add_argument(
        "--sem-ia",
        action="store_true",
        help="Só análise por regras (ignora GEMINI_API_KEY).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gera o relatório e não envia e-mail.",
    )
    args = parser.parse_args(argv)

    _carregar_secrets_locais()

    from calculadora.relatorio import enviar_relatorio, gerar_relatorio

    try:
        rel = gerar_relatorio(usar_ia=not args.sem_ia)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO ao gerar relatório: {exc}", file=sys.stderr)
        return 1

    n = len(rel.metricas)
    print(
        f"Relatório {rel.gerado_em.isoformat(timespec='seconds')} · "
        f"{n} ação(ões) · motor={rel.motor}"
    )
    if rel.avisos:
        for a in rel.avisos:
            print(f"  aviso: {a}")

    if args.dry_run:
        print("Dry-run: e-mail não enviado.")
        return 0

    try:
        enviar_relatorio(rel)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO ao enviar e-mail: {exc}", file=sys.stderr)
        return 1

    print("E-mail enviado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
