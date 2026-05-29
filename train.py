"""
train.py — Treino offline do classificador de predição de FAULT.

Fluxo:
  1. Lê todos os snapshots do SQLite (events.db)
  2. Feature engineering: sliding window de WINDOW_SIZE amostras anteriores
  3. Target: 1 se o próximo status for FAULT, 0 caso contrário
  4. Treina XGBoost com class_weight para lidar com desbalanceamento
  5. Avalia com classification_report e ROC-AUC
  6. Salva model.pkl e feature_columns.json na pasta corrente

Executar manualmente após acumular dados suficientes:
  python train.py [--device prensa-001] [--window 5] [--min-rows 100]
"""

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

# Importa db.py do mesmo diretório
sys.path.insert(0, str(Path(__file__).parent))
from db import fetch_all_snapshots, count_snapshots, init_db

# ── Constantes configuráveis ──────────────────────────────────────────────────
WINDOW_SIZE = 5          # quantos snapshots anteriores compõem as features
STATUS_TYPES = ["RUNNING", "BLOCKED", "FAULT", "IDLE"]
MODEL_PATH   = Path(__file__).parent / "model.pkl"
FEAT_PATH    = Path(__file__).parent / "feature_columns.json"


# ── Feature Engineering ───────────────────────────────────────────────────────

def build_dataset(rows: list[dict], window: int) -> tuple[pd.DataFrame, pd.Series]:
    """
    Converte a série temporal em um dataset supervisionado.

    Para cada posição i >= window:
      - Features: os `window` snapshots anteriores (i-window .. i-1), com:
          * status one-hot encoded (4 estados)
          * delta (incremento de produção)
          * working, blocked, failed (% do simulador; -1 se ausente)
          * razão blocked/working (proxy de pressão de fila)
      - Target: 1 se rows[i]['status'] == 'FAULT', 0 caso contrário

    Retorna (X, y) como DataFrame e Series.
    """
    le = LabelEncoder()
    le.fit(STATUS_TYPES)

    records = []
    targets = []

    for i in range(window, len(rows)):
        window_rows = rows[i - window : i]
        next_status  = rows[i]["status"]

        feat: dict = {}
        for j, r in enumerate(window_rows):
            prefix = f"t-{window - j}"  # t-5, t-4, ..., t-1

            # One-hot para status (evita ordinal implícito)
            for s in STATUS_TYPES:
                feat[f"{prefix}_is_{s}"] = 1 if r["status"] == s else 0

            feat[f"{prefix}_delta"]   = r["delta"] or 0
            feat[f"{prefix}_working"] = r["working"]  if r["working"]  is not None else -1
            feat[f"{prefix}_blocked"] = r["blocked"]  if r["blocked"]  is not None else -1
            feat[f"{prefix}_failed"]  = r["failed"]   if r["failed"]   is not None else -1

            # Feature derivada: pressão de bloqueio
            w = r["working"] or 0.001
            b = r["blocked"] or 0.0
            feat[f"{prefix}_block_ratio"] = b / w

        records.append(feat)
        targets.append(1 if next_status == "FAULT" else 0)

    X = pd.DataFrame(records).fillna(-1)
    y = pd.Series(targets, name="fault")
    return X, y


# ── Treino ────────────────────────────────────────────────────────────────────

def train(device_id: str, window: int, min_rows: int) -> None:
    init_db()

    n = count_snapshots(device_id)
    print(f"[train] Snapshots encontrados para '{device_id}': {n}")

    if n < min_rows:
        print(
            f"[train] ERRO: mínimo de {min_rows} snapshots exigido para treino confiável. "
            f"Execute mais simulações para acumular dados."
        )
        sys.exit(1)

    rows = fetch_all_snapshots(device_id)
    X, y = build_dataset(rows, window)

    print(f"[train] Dataset: {len(X)} amostras | FAULT={y.sum()} ({y.mean()*100:.1f}%)")

    if y.sum() < 5:
        print("[train] AVISO: menos de 5 amostras positivas (FAULT). "
              "O modelo pode não generalizar. Acumule mais dados com falhas.")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y if y.sum() >= 2 else None
    )

    # Peso para compensar desbalanceamento (FAULT é evento raro)
    neg = (y_train == 0).sum()
    pos = (y_train == 1).sum() or 1
    scale_pos = neg / pos

    model = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=scale_pos,   # compensa desbalanceamento
        use_label_encoder=False,
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )
    model.fit(X_train, y_train)

    # ── Avaliação ──────────────────────────────────────────────────────────────
    y_pred  = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    print("\n── Classification Report ─────────────────────────────")
    print(classification_report(y_test, y_pred, target_names=["Normal", "FAULT"],
                                 zero_division=0))

    if len(np.unique(y_test)) > 1:
        auc = roc_auc_score(y_test, y_proba)
        print(f"ROC-AUC: {auc:.4f}")
    else:
        print("ROC-AUC: indisponível (apenas uma classe no conjunto de teste)")

    # ── Feature importance (top 10) ───────────────────────────────────────────
    importance = pd.Series(model.feature_importances_, index=X.columns)
    print("\n── Top 10 features mais importantes ─────────────────")
    print(importance.nlargest(10).to_string())

    # ── Persistência ──────────────────────────────────────────────────────────
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "window": window, "device_id": device_id}, f)

    with open(FEAT_PATH, "w") as f:
        json.dump(list(X.columns), f, indent=2)

    print(f"\n[train] Modelo salvo em: {MODEL_PATH}")
    print(f"[train] Features salvas em: {FEAT_PATH}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Treino offline — predição de FAULT")
    parser.add_argument("--device", default="prensa-001",
                        help="device_id a treinar (default: prensa-001)")
    parser.add_argument("--window", type=int, default=WINDOW_SIZE,
                        help=f"tamanho da janela deslizante (default: {WINDOW_SIZE})")
    parser.add_argument("--min-rows", type=int, default=100,
                        help="mínimo de snapshots para iniciar treino (default: 100)")
    args = parser.parse_args()
    train(args.device, args.window, args.min_rows)
