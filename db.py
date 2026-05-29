"""
db.py — Camada de persistência SQLite para snapshots da fábrica.

Schema:
  machine_snapshots: uma linha por snapshot recebido no /data/batch,
                     com todos os campos relevantes para o treinamento ML.

Uso:
  from db import init_db, insert_snapshot
  init_db()
  insert_snapshot("prensa-001", "FAULT", 628, 65.5, 23.71, 10.79)
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "events.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Cria as tabelas se não existirem. Idempotente."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS machine_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL    NOT NULL,          -- unix epoch (float)
                device_id   TEXT    NOT NULL,
                status      TEXT    NOT NULL,          -- RUNNING | BLOCKED | FAULT | IDLE
                producao    INTEGER NOT NULL DEFAULT 0,
                delta       INTEGER NOT NULL DEFAULT 0, -- incremento desde snapshot anterior
                working     REAL,                      -- % tempo working (vindo do simulador)
                blocked     REAL,                      -- % tempo blocked
                failed      REAL                       -- % tempo failed
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_device_ts
            ON machine_snapshots (device_id, ts)
        """)


def insert_snapshot(
    device_id: str,
    status: str,
    producao: int,
    delta: int,
    working: float | None,
    blocked: float | None,
    failed: float | None,
) -> None:
    """Insere um snapshot no banco. Thread-safe via conexão por chamada."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO machine_snapshots
                (ts, device_id, status, producao, delta, working, blocked, failed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (time.time(), device_id, status, producao, delta, working, blocked, failed),
        )


def fetch_all_snapshots(device_id: str) -> list[dict]:
    """Retorna todos os snapshots de um device ordenados por ts. Para treino."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM machine_snapshots WHERE device_id = ? ORDER BY ts ASC",
            (device_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_snapshots(device_id: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM machine_snapshots WHERE device_id = ?",
            (device_id,),
        ).fetchone()[0]
