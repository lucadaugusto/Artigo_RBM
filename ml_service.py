"""
ml_service.py — Serviço de inferência rodando na EC2 (container Docker)

Novidades em relação à versão local:
  - POST /notify  : recebe notificações de subscription do Orion (NGSI-v2)
                    atualiza o buffer por device e recalcula a predição
  - GET  /last_predictions : retorna cache das últimas predições (consumido pelo dashboard)
  - MODEL_PATH e FEATURE_PATH lidos de variáveis de ambiente (volume Docker)

Dockerfile esperado (./ml_service/Dockerfile):
  FROM python:3.11-slim
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt
  COPY . .
  CMD ["uvicorn", "ml_service:app", "--host", "0.0.0.0", "--port", "5001"]
"""

import json
import os
import pickle
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── Configuração por variável de ambiente ────────────────────────────────────
MODEL_PATH   = Path(os.getenv("MODEL_PATH",   "model.pkl"))
FEATURE_PATH = Path(os.getenv("FEATURE_PATH", "feature_columns.json"))
THRESHOLD    = float(os.getenv("FAULT_THRESHOLD", "0.60"))
ML_WINDOW    = int(os.getenv("ML_WINDOW", "5"))

STATUS_TYPES = ["RUNNING", "BLOCKED", "FAULT", "IDLE"]

app = FastAPI(title="Fault Prediction Service — EC2", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Estado global ────────────────────────────────────────────────────────────
_model        = None
_feature_cols = None
_window       = ML_WINDOW

# Buffer circular por device: guarda os últimos N snapshots para inferência
# Populado via POST /notify (subscription do Orion)
_ml_buffer: dict[str, deque] = {}

# Cache das últimas predições, consultado pelo dashboard
_last_predictions: dict[str, dict] = {}


def load_model() -> bool:
    global _model, _feature_cols, _window
    if not MODEL_PATH.exists():
        return False
    with open(MODEL_PATH, "rb") as f:
        payload = pickle.load(f)
    _model        = payload["model"]
    _window       = payload.get("window", ML_WINDOW)
    with open(FEATURE_PATH) as f:
        _feature_cols = json.load(f)
    return True


@app.on_event("startup")
def startup():
    ok = load_model()
    status = "carregado" if ok else "AUSENTE (execute train.py)"
    print(f"[ml_service] Modelo: {status} | window={_window} | threshold={THRESHOLD}")


# ── Feature engineering (idêntico ao train.py) ───────────────────────────────

def build_feature_vector(snapshots: list[dict], window: int) -> dict:
    feat: dict = {}
    for j, r in enumerate(list(snapshots)[-window:]):
        prefix = f"t-{window - j}"
        for s in STATUS_TYPES:
            feat[f"{prefix}_is_{s}"] = 1 if r.get("status") == s else 0
        feat[f"{prefix}_delta"]   = r.get("delta", 0) or 0
        feat[f"{prefix}_working"] = r["working"] if r.get("working") is not None else -1
        feat[f"{prefix}_blocked"] = r["blocked"] if r.get("blocked") is not None else -1
        feat[f"{prefix}_failed"]  = r["failed"]  if r.get("failed")  is not None else -1
        w = r.get("working") or 0.001
        b = r.get("blocked") or 0.0
        feat[f"{prefix}_block_ratio"] = b / w
    return feat


def run_prediction(device_id: str) -> dict | None:
    if _model is None or _feature_cols is None:
        return None
    buf = _ml_buffer.get(device_id)
    if not buf or len(buf) < _window:
        return None
    import pandas as pd
    feat_dict = build_feature_vector(buf, _window)
    feat_row  = {col: feat_dict.get(col, -1) for col in _feature_cols}
    X = pd.DataFrame([feat_row])
    prob = float(_model.predict_proba(X)[0, 1])
    result = {
        "device_id":         device_id,
        "fault_probability": round(prob, 4),
        "alert":             prob >= THRESHOLD,
        "threshold":         THRESHOLD,
        "model_window":      _window,
        "buffer_size":       len(buf),
    }
    _last_predictions[device_id] = result
    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status":       "ok",
        "model_loaded": _model is not None,
        "window":       _window,
        "threshold":    THRESHOLD,
        "devices_buffered": list(_ml_buffer.keys()),
    }


@app.post("/reload")
def reload_model():
    ok = load_model()
    if not ok:
        raise HTTPException(status_code=404, detail="model.pkl não encontrado")
    return {"reloaded": True, "window": _window}


@app.post("/notify")
async def orion_notify(request: Request):
    """
    Recebe notificações de subscription do Orion Context Broker (NGSI-v2).
    Formato do payload:
    {
      "subscriptionId": "...",
      "data": [
        {
          "id": "urn:ngsi-v2:Machine:prensa-001",
          "type": "Machine",
          "status":         {"type": "Text",    "value": "BLOCKED", ...},
          "productionCount":{"type": "Integer", "value": 628, ...},
          "workingPct":     {"type": "Number",  "value": 65.5, ...},
          "blockedPct":     {"type": "Number",  "value": 23.7, ...},
          "failedPct":      {"type": "Number",  "value": 10.8, ...}
        }
      ]
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON inválido")

    predictions = []
    for entity in body.get("data", []):
        entity_id = entity.get("id", "")
        # Extrai device_id do entity_id: urn:ngsi-v2:Machine:prensa-001 → prensa-001
        device_id = entity_id.split(":")[-1] if ":" in entity_id else entity_id

        def val(attr):
            v = entity.get(attr, {})
            return v.get("value") if isinstance(v, dict) else v

        snapshot = {
            "status":  val("status")         or "IDLE",
            "delta":   0,
            "working": val("workingPct"),
            "blocked": val("blockedPct"),
            "failed":  val("failedPct"),
        }

        if device_id not in _ml_buffer:
            _ml_buffer[device_id] = deque(maxlen=_window * 3)
        _ml_buffer[device_id].append(snapshot)

        pred = run_prediction(device_id)
        if pred:
            predictions.append(pred)
            if pred["alert"]:
                print(f"[ml_service] ALERT {device_id}: "
                      f"fault_prob={pred['fault_probability']:.3f}")

    return {"processed": len(body.get("data", [])), "predictions": predictions}


@app.get("/last_predictions")
def last_predictions():
    """
    Retorna o cache das últimas predições por device.
    Consumido pelo dashboard via polling a cada 3 segundos.
    """
    return _last_predictions


@app.get("/last_predictions/{device_id}")
def last_prediction_device(device_id: str):
    pred = _last_predictions.get(device_id)
    if not pred:
        return {"device_id": device_id, "fault_probability": 0,
                "alert": False, "threshold": THRESHOLD,
                "model_window": _window, "buffer_size": 0}
    return pred


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("ml_service:app", host="0.0.0.0", port=5001, reload=False)
