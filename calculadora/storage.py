"""Persistência local das ações em SQLite."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_LOCAL_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "acoes.db"


def resolve_db_path(db_path: Path | None = None) -> Path:
    """
    Resolve o caminho do SQLite.
    Ordem: argumento explícito → DB_PATH → DATA_DIR/acoes.db → data/ local.
    """
    if db_path is not None:
        return Path(db_path)
    explicit = (os.environ.get("DB_PATH") or "").strip()
    if explicit:
        return Path(explicit)
    data_dir = (os.environ.get("DATA_DIR") or "").strip()
    if data_dir:
        return Path(data_dir) / "acoes.db"
    return _LOCAL_DEFAULT


# Compat: caminho padrão sem variáveis de ambiente
DEFAULT_DB_PATH = _LOCAL_DEFAULT


@dataclass
class AcaoSalva:
    ticker: str
    preco: float
    dy: float  # percentual, ex.: 6.0
    proventos: str
    updated_at: str


@dataclass
class PosicaoSalva:
    ticker: str
    quantidade: float
    preco_medio: float
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS posicoes (
            ticker TEXT PRIMARY KEY COLLATE NOCASE,
            quantidade REAL NOT NULL,
            preco_medio REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def listar_acoes(db_path: Path | None = None) -> list[AcaoSalva]:
    path = resolve_db_path(db_path)
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
    path = resolve_db_path(db_path)
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
    path = resolve_db_path(db_path)
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
    path = resolve_db_path(db_path)
    ticker = ticker.strip().upper()
    if not ticker:
        return False
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM acoes WHERE ticker = ?", (ticker,))
        conn.commit()
        return cur.rowcount > 0


def listar_posicoes(db_path: Path | None = None) -> list[PosicaoSalva]:
    path = resolve_db_path(db_path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT ticker, quantidade, preco_medio, updated_at "
            "FROM posicoes ORDER BY ticker COLLATE NOCASE"
        ).fetchall()
    return [
        PosicaoSalva(
            ticker=row["ticker"],
            quantidade=float(row["quantidade"]),
            preco_medio=float(row["preco_medio"]),
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def obter_posicao(ticker: str, db_path: Path | None = None) -> PosicaoSalva | None:
    path = resolve_db_path(db_path)
    ticker = ticker.strip().upper()
    if not ticker:
        return None
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT ticker, quantidade, preco_medio, updated_at "
            "FROM posicoes WHERE ticker = ?",
            (ticker,),
        ).fetchone()
    if row is None:
        return None
    return PosicaoSalva(
        ticker=row["ticker"],
        quantidade=float(row["quantidade"]),
        preco_medio=float(row["preco_medio"]),
        updated_at=row["updated_at"],
    )


def salvar_posicao(
    *,
    ticker: str,
    quantidade: float,
    preco_medio: float,
    db_path: Path | None = None,
) -> PosicaoSalva:
    path = resolve_db_path(db_path)
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Informe o ticker antes de salvar a posição.")
    if quantidade <= 0:
        raise ValueError("Quantidade deve ser maior que zero.")
    if preco_medio < 0:
        raise ValueError("Preço médio inválido.")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO posicoes (ticker, quantidade, preco_medio, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                quantidade = excluded.quantidade,
                preco_medio = excluded.preco_medio,
                updated_at = excluded.updated_at
            """,
            (ticker, float(quantidade), float(preco_medio), now),
        )
        conn.commit()

    return PosicaoSalva(
        ticker=ticker,
        quantidade=float(quantidade),
        preco_medio=float(preco_medio),
        updated_at=now,
    )


def excluir_posicao(ticker: str, db_path: Path | None = None) -> bool:
    path = resolve_db_path(db_path)
    ticker = ticker.strip().upper()
    if not ticker:
        return False
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM posicoes WHERE ticker = ?", (ticker,))
        conn.commit()
        return cur.rowcount > 0


def substituir_posicoes(
    posicoes: list[tuple[str, float, float]],
    *,
    db_path: Path | None = None,
) -> list[PosicaoSalva]:
    """Substitui todas as posições pelo lote informado (ticker, qty, preço médio)."""
    path = resolve_db_path(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    limpas: list[tuple[str, float, float]] = []
    for ticker, quantidade, preco_medio in posicoes:
        t = (ticker or "").strip().upper()
        if not t:
            raise ValueError("Ticker vazio no lote de posições.")
        q = float(quantidade)
        pm = float(preco_medio)
        if q <= 0:
            raise ValueError(f"Quantidade inválida para {t}.")
        if pm < 0:
            raise ValueError(f"Preço médio inválido para {t}.")
        limpas.append((t, q, pm))

    with _connect(path) as conn:
        conn.execute("DELETE FROM posicoes")
        for t, q, pm in limpas:
            conn.execute(
                """
                INSERT INTO posicoes (ticker, quantidade, preco_medio, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (t, q, pm, now),
            )
        conn.commit()

    return [
        PosicaoSalva(ticker=t, quantidade=q, preco_medio=pm, updated_at=now)
        for t, q, pm in limpas
    ]
