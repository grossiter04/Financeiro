"""Persistência local das ações em SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "acoes.db"


@dataclass
class AcaoSalva:
    ticker: str
    preco: float
    dy: float  # percentual, ex.: 6.0
    proventos: str
    updated_at: str


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS acoes (
            ticker TEXT PRIMARY KEY COLLATE NOCASE,
            preco REAL NOT NULL,
            dy REAL NOT NULL,
            proventos TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def listar_acoes(db_path: Path | None = None) -> list[AcaoSalva]:
    path = db_path or DEFAULT_DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT ticker, preco, dy, proventos, updated_at "
            "FROM acoes ORDER BY ticker COLLATE NOCASE"
        ).fetchall()
    return [
        AcaoSalva(
            ticker=row["ticker"],
            preco=float(row["preco"]),
            dy=float(row["dy"]),
            proventos=row["proventos"] or "",
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def listar_tickers(db_path: Path | None = None) -> list[str]:
    return [a.ticker for a in listar_acoes(db_path)]


def obter_acao(ticker: str, db_path: Path | None = None) -> AcaoSalva | None:
    path = db_path or DEFAULT_DB_PATH
    ticker = ticker.strip().upper()
    if not ticker:
        return None
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT ticker, preco, dy, proventos, updated_at FROM acoes WHERE ticker = ?",
            (ticker,),
        ).fetchone()
    if row is None:
        return None
    return AcaoSalva(
        ticker=row["ticker"],
        preco=float(row["preco"]),
        dy=float(row["dy"]),
        proventos=row["proventos"] or "",
        updated_at=row["updated_at"],
    )


def salvar_acao(
    *,
    ticker: str,
    preco: float,
    dy: float,
    proventos: str,
    db_path: Path | None = None,
) -> AcaoSalva:
    path = db_path or DEFAULT_DB_PATH
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Informe o ticker antes de salvar.")
    if preco < 0:
        raise ValueError("Preço inválido.")
    if dy <= 0:
        raise ValueError("DY desejado deve ser maior que zero.")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO acoes (ticker, preco, dy, proventos, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                preco = excluded.preco,
                dy = excluded.dy,
                proventos = excluded.proventos,
                updated_at = excluded.updated_at
            """,
            (ticker, float(preco), float(dy), proventos or "", now),
        )
        conn.commit()

    return AcaoSalva(
        ticker=ticker,
        preco=float(preco),
        dy=float(dy),
        proventos=proventos or "",
        updated_at=now,
    )


def excluir_acao(ticker: str, db_path: Path | None = None) -> bool:
    path = db_path or DEFAULT_DB_PATH
    ticker = ticker.strip().upper()
    if not ticker:
        return False
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM acoes WHERE ticker = ?", (ticker,))
        conn.commit()
        return cur.rowcount > 0
