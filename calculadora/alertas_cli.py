"""CLI: python -m calculadora.alertas_cli [--dry-run|--test-email|--force]."""

from __future__ import annotations

import argparse
import sys

from calculadora.alertas import (
    _smtp_config_from_env,
    checar_e_avisar,
    enviar_email,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checa preços e avisa por e-mail/Telegram")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só imprime; não envia aviso",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignora anti-spam do dia e reenvia se houver sinais",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Envia um e-mail de teste (valida SMTP) e sai",
    )
    args = parser.parse_args(argv)

    if args.test_email:
        smtp = _smtp_config_from_env()
        if smtp is None:
            print(
                "SMTP incompleto. Defina SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO.",
                file=sys.stderr,
            )
            return 1
        enviar_email(
            "Teste do vigilante Financeiro.\nSe você recebeu isto, o SMTP está ok.",
            host=str(smtp["host"]),
            port=int(smtp["port"]),
            user=str(smtp["user"]),
            password=str(smtp["password"]),
            para=str(smtp["para"]),
            de=str(smtp["de"]),
            assunto="Teste: alertas Financeiro",
        )
        print(f"E-mail de teste enviado para {smtp['para']}")
        return 0

    result = checar_e_avisar(dry_run=args.dry_run, forcar=args.force)
    for e in result.erros:
        print(f"AVISO: {e}", file=sys.stderr)
    print(
        f"Sinais={len(result.sinais)} enviados={len(result.enviados)} "
        f"spam_ignorado={len(result.ignorados_spam)}"
    )
    if result.enviados:
        print("Enviados:", ", ".join(result.enviados))
    elif result.sinais and not args.dry_run:
        print(
            "Houve sinais, mas já avisados hoje ou o envio falhou — veja log acima.",
            file=sys.stderr,
        )
    elif not result.sinais:
        print("Nenhuma ação barata agora — por isso não há e-mail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
