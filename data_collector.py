"""
data_collector.py
=================
Patch de persistência para o dashboard Flask existente.

COMO USAR:
  Substitua o app.py original por este, ou importe-o como módulo.
  A única adição é a camada SQLite que grava cada snapshot recebido
  em /batch com timestamp absoluto (unix epoch), acumulando runs
  consecutivos sem zerar entre sessões.

  Banco gerado: fault_dataset.db (na mesma pasta do script)
  Tabela:       snapshots
"""

import time
import sqlite3
import json
import os
from collections import deque, defaultdict
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuração do banco SQLite
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "fault_dataset.db")


def init_db():
    """Cria a tabela de snapshots se não existir."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      INTEGER NOT NULL,       -- identificador da sessão/run
            epoch       REAL    NOT NULL,       -- unix timestamp absoluto
            device_id   TEXT    NOT NULL,
            status      TEXT    NOT NULL,
            producao    INTEGER NOT NULL,
            delta       INTEGER NOT NULL,
            working     REAL,                  -- % direto do Plant Sim (pode ser NULL)
            blocked     REAL,
            failed      REAL,
            ocupacao    INTEGER                -- apenas para buffer-001
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_device ON snapshots(device_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run    ON snapshots(run_id)")
    conn.commit()
    conn.close()


def get_next_run_id() -> int:
    """Retorna o próximo run_id sequencial."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT MAX(run_id) FROM snapshots").fetchone()
    conn.close()
    current = row[0] if row[0] is not None else 0
    return current + 1


init_db()

# ---------------------------------------------------------------------------
# Estado em memória (idêntico ao app.py original)
# ---------------------------------------------------------------------------

DEVICES = ["prensa-001", "torno-001"]
STATUS_TYPES = ["RUNNING", "BLOCKED", "FAULT", "IDLE"]

machines = {}
history = {d: deque(maxlen=120) for d in DEVICES}
status_counts = {d: defaultdict(int) for d in DEVICES}
last_producao = {d: None for d in DEVICES}
session_start = None
current_run_id = get_next_run_id()   # novo run_id a cada arranque do processo

# ---------------------------------------------------------------------------
# HTML (mantido idêntico ao original — omitido aqui por brevidade)
# Cole aqui o HTML do seu app.py original
# ---------------------------------------------------------------------------

HTML = "<h1>Cole aqui o HTML do app.py original</h1>"

# ---------------------------------------------------------------------------
# Helpers (idênticos ao original)
# ---------------------------------------------------------------------------


def _avail_from_stats(m, counts):
    if "working" in m:
        return float(m["working"])
    total = sum(counts.values()) or 1
    return round(counts.get("RUNNING", 0) / total * 100, 1)


def _status_pct(m, counts):
    if "working" in m:
        wp = float(m.get("working", 0))
        bp = float(m.get("blocked", 0))
        fp = float(m.get("failed", 0))
        ip = max(0.0, round(100.0 - wp - bp - fp, 1))
        return {"RUNNING": round(wp, 1), "BLOCKED": round(bp, 1),
                "FAULT": round(fp, 1), "IDLE": ip}
    total = sum(counts.values()) or 1
    return {s: round(counts.get(s, 0) / total * 100, 1) for s in STATUS_TYPES}

# ---------------------------------------------------------------------------
# Persistência SQLite
# ---------------------------------------------------------------------------


def _persist_snapshot(run_id: int, epoch: float, device_id: str, snap: dict):
    """
    Grava um snapshot na tabela SQLite.
    Chamado a cada item recebido em /data/batch.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO snapshots
          (run_id, epoch, device_id, status, producao, delta,
           working, blocked, failed, ocupacao)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        run_id,
        epoch,
        device_id,
        snap.get("status", "IDLE"),
        snap.get("producao", 0),
        snap.get("delta", 0),
        snap.get("working"),          # NULL se não vier do Plant Sim
        snap.get("blocked"),
        snap.get("failed"),
        snap.get("ocupacao"),
    ))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return HTML


@app.route("/data/batch", methods=["POST"])
def receive_batch():
    global session_start, current_run_id

    items = request.get_json(force=True)
    if not isinstance(items, list):
        return {"ok": False}, 400

    if session_start is None:
        session_start = time.time()
        # Novo run apenas quando a sessão é reiniciada via /reset
        # (current_run_id já foi incrementado no arranque ou no reset)

    now_ts = time.strftime("%H:%M:%S")
    now_epoch = time.time()

    for item in items:
        device_id = item.get("device_id")
        if not device_id:
            continue

        # --- buffer ---
        if device_id == "buffer-001":
            try:
                snap = {"ocupacao": int(item.get("ocupacao", 0)),
                        "updated": now_ts}
                machines["buffer-001"] = snap
                _persist_snapshot(current_run_id, now_epoch, device_id,
                                  {"ocupacao": snap["ocupacao"]})
            except (ValueError, TypeError):
                pass
            continue

        if device_id not in DEVICES:
            continue

        producao = item.get("producao", 0)
        status   = item.get("status", "IDLE")
        prev     = last_producao[device_id]
        delta    = (producao - prev) if prev is not None else 0
        last_producao[device_id] = producao

        snapshot = {k: v for k, v in item.items() if k != "device_id"}
        snapshot.update({"timestamp": now_ts, "delta": delta})

        machines[device_id] = {**snapshot, "device_id": device_id,
                               "updated": now_ts}
        status_counts[device_id][status] += 1
        history[device_id].append(snapshot)

        # ← ÚNICA ADIÇÃO ao fluxo original: persistência em disco
        _persist_snapshot(current_run_id, now_epoch, device_id, snapshot)

    return {"ok": True}, 200


@app.route("/reset", methods=["POST"])
def reset():
    global session_start, current_run_id
    machines.clear()
    for d in DEVICES:
        history[d].clear()
        status_counts[d].clear()
        last_producao[d] = None
    session_start = None
    # Incrementa run_id para a próxima sessão
    current_run_id = get_next_run_id()
    return {"ok": True}, 200


@app.route("/api/data")
def api_data():
    """Idêntico ao original — cole aqui o conteúdo da rota /api/data do app.py."""
    return jsonify({"msg": "Cole aqui a rota api_data do app.py original"})


@app.route("/db/info")
def db_info():
    """Endpoint de diagnóstico: mostra quantos snapshots estão gravados."""
    conn = sqlite3.connect(DB_PATH)
    total     = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    per_run   = conn.execute(
        "SELECT run_id, COUNT(*) FROM snapshots GROUP BY run_id ORDER BY run_id"
    ).fetchall()
    per_device = conn.execute(
        "SELECT device_id, COUNT(*) FROM snapshots GROUP BY device_id"
    ).fetchall()
    faults    = conn.execute(
        "SELECT device_id, COUNT(*) FROM snapshots WHERE status='FAULT' "
        "GROUP BY device_id"
    ).fetchall()
    conn.close()
    return jsonify({
        "total_snapshots": total,
        "current_run_id":  current_run_id,
        "per_run":         dict(per_run),
        "per_device":      dict(per_device),
        "fault_counts":    dict(faults),
    })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[collector] DB: {DB_PATH}")
    print(f"[collector] Run ID atual: {current_run_id}")
    print("Dashboard em: http://localhost:5000")
    print("Diagnóstico DB em: http://localhost:5000/db/info")
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
