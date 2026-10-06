"""Gera e envia o relatório diário de notícias. Uso: python -m calculadora.relatorio_cli [--dry-run]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from calculadora.relatorio import enviar_relatorio, gerar_relatorio, renderizar_html


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Relatório diário de notícias das suas ações")
    p.add_argument("--dry-run", action="store_true", help="Não envia e-mail, só gera o arquivo HTML")
    p.add_argument("--out", type=Path, default=None, help="Salvar HTML neste arquivo")
    p.add_argument("--sem-ia", action="store_true", help="Não chamar a IA (análise simples)")
    p.add_argument("--horas", type=int, default=36, help="Janela de notícias em horas (padrão 36)")
    args = p.parse_args(argv)

    rel = gerar_relatorio(usar_ia=not args.sem_ia, horas=args.horas)
    print(
        f"Acoes={len(rel.metricas)} noticias_relevantes={len(rel.noticias)} "
        f"motor={rel.motor} avisos={len(rel.avisos)}"
    )
    for a in rel.avisos[:15]:
        print(f"  aviso: {a}".encode("ascii", "replace").decode("ascii"))

    out = args.out or (Path("alertas/relatorio.html") if args.dry_run else None)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(renderizar_html(rel), encoding="utf-8")
        print(f"HTML salvo em {out}")

    if args.dry_run:
        return 0
    try:
        enviar_relatorio(rel)
    except Exception as exc:  # noqa: BLE001
        print(f"Falha ao enviar e-mail: {exc}", file=sys.stderr)
        return 1
    print("E-mail enviado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
