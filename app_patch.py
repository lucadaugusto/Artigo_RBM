"""
app_patch.py — Diff comentado das alterações necessárias no app.py original.

NÃO é um arquivo completo. Mostra exatamente o que muda e onde.
Aplique manualmente no seu app.py existente.

Alterações:
  1. Imports adicionais (topo do arquivo)
  2. Inicialização do DB no startup
  3. Buffer circular por device para o ML service
  4. Função auxiliar _request_prediction()
  5. Bloco adicional dentro de /data/batch após salvar em machines[]
  6. Rota /api/prediction (lida pelo dashboard via polling)
"""

# ════════════════════════════════════════════════════════════════════════════════
# 1. ADICIONAR AOS IMPORTS (topo do arquivo, junto com os outros imports)
# ════════════════════════════════════════════════════════════════════════════════

import httpx                          # cliente HTTP assíncrono para o ML service
from db import init_db, insert_snapshot  # módulo de persistência SQLite

ML_SERVICE_URL = "http://localhost:5001"
ML_WINDOW      = 5                    # deve coincidir com o --window usado no treino

# Buffer circular por device: guarda os últimos ML_WINDOW snapshots para inferência
# Estrutura: { "prensa-001": deque([snap1, snap2, ...], maxlen=ML_WINDOW) }
from collections import deque
ml_buffer: dict[str, deque] = {d: deque(maxlen=ML_WINDOW) for d in DEVICES}

# Cache da última predição por device (evita requisição a cada poll do dashboard)
last_prediction: dict[str, dict] = {}


# ════════════════════════════════════════════════════════════════════════════════
# 2. LOGO APÓS A CRIAÇÃO DO app = Flask(__name__), ANTES DAS ROTAS:
# ════════════════════════════════════════════════════════════════════════════════

# Inicializa o banco SQLite ao subir o Flask
with app.app_context():
    init_db()


# ════════════════════════════════════════════════════════════════════════════════
# 3. NOVA FUNÇÃO AUXILIAR — adicionar antes das rotas
# ════════════════════════════════════════════════════════════════════════════════

def _request_prediction(device_id: str) -> dict | None:
    """
    Envia os últimos ML_WINDOW snapshots do buffer para o ml_service e retorna
    a predição. Retorna None silenciosamente se o serviço não estiver disponível
    (não queremos derrubar o Flask se o ML service estiver fora).
    """
    buf = list(ml_buffer.get(device_id, []))
    if len(buf) < ML_WINDOW:
        return None
    try:
        payload = {
            "device_id": device_id,
            "snapshots": [
                {
                    "status":  s.get("status", "IDLE"),
                    "delta":   s.get("delta", 0),
                    "working": s.get("working"),
                    "blocked": s.get("blocked"),
                    "failed":  s.get("failed"),
                }
                for s in buf
            ],
        }
        resp = httpx.post(
            f"{ML_SERVICE_URL}/predict",
            json=payload,
            timeout=1.0,        # timeout agressivo: não bloqueia o Flask
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════════════
# 4. DENTRO DO ENDPOINT /data/batch — no bloco "if device_id not in DEVICES: continue"
#    APÓS a linha: machines[device_id] = {**snapshot, "device_id": device_id, "updated": now}
#    ADICIONAR estas linhas:
# ════════════════════════════════════════════════════════════════════════════════

# --- INÍCIO DO BLOCO A INSERIR ---

# Persiste no SQLite
insert_snapshot(
    device_id = device_id,
    status    = status,
    producao  = producao,
    delta     = delta,
    working   = item.get("working"),
    blocked   = item.get("blocked"),
    failed    = item.get("failed"),
)

# Atualiza buffer do ML service com o snapshot atual
ml_buffer[device_id].append({
    "status":  status,
    "delta":   delta,
    "working": item.get("working"),
    "blocked": item.get("blocked"),
    "failed":  item.get("failed"),
})

# Solicita predição e armazena resultado em cache
pred = _request_prediction(device_id)
if pred:
    last_prediction[device_id] = pred

# --- FIM DO BLOCO A INSERIR ---


# ════════════════════════════════════════════════════════════════════════════════
# 5. NOVA ROTA — adicionar junto com as outras rotas
#    O dashboard vai consultar este endpoint a cada 2 segundos (junto com /api/data)
# ════════════════════════════════════════════════════════════════════════════════

from flask import jsonify  # já importado, só para referência

@app.route("/api/prediction")
def api_prediction():
    """
    Retorna a última predição de FAULT para cada device.

    Response example:
    {
      "prensa-001": {
        "fault_probability": 0.83,
        "alert": true,
        "threshold": 0.6
      },
      "torno-001": null
    }
    """
    result = {}
    for device_id in DEVICES:
        pred = last_prediction.get(device_id)
        if pred:
            result[device_id] = {
                "fault_probability": pred.get("fault_probability", 0),
                "alert":             pred.get("alert", False),
                "threshold":         pred.get("threshold", 0.6),
            }
        else:
            result[device_id] = None
    return jsonify(result)


# ════════════════════════════════════════════════════════════════════════════════
# 6. MODIFICAÇÃO NO DASHBOARD HTML — adicionar o alerta visual
#
# No bloco <script> do HTML, dentro da função update(), após a chamada
# a updateNode(), adicionar:
# ════════════════════════════════════════════════════════════════════════════════

JAVASCRIPT_PATCH = """
// ── Predição de FAULT ────────────────────────────────────────────────────────
async function updatePrediction() {
  let p;
  try { p = await (await fetch('/api/prediction')).json(); } catch(e) { return; }

  for (const [devId, pred] of Object.entries(p)) {
    const shortId = devId.split('-')[0];   // 'prensa' ou 'torno'
    const badge   = document.getElementById('fault-badge-' + shortId);
    if (!badge) continue;

    if (pred && pred.alert) {
      const pct = (pred.fault_probability * 100).toFixed(0);
      badge.textContent = '⚠ FAULT ' + pct + '%';
      badge.style.display = 'inline-block';
    } else if (pred) {
      const pct = (pred.fault_probability * 100).toFixed(0);
      badge.textContent = pct + '% fault risk';
      badge.style.display = (pred.fault_probability > 0.3) ? 'inline-block' : 'none';
    } else {
      badge.style.display = 'none';
    }
  }
}

// Chamar junto com update() no setInterval:
// setInterval(() => { update(); updatePrediction(); }, 2000);
// updatePrediction();
"""

# ════════════════════════════════════════════════════════════════════════════════
# 7. ADICIONAR NO HTML — dentro de cada .pnode-sub (sob a caixa da máquina):
#
# Para a PRENSA:
#   <div id="fault-badge-prensa" class="fault-badge" style="display:none"></div>
#
# Para o TORNO:
#   <div id="fault-badge-torno" class="fault-badge" style="display:none"></div>
#
# E adicionar ao <style>:
# ════════════════════════════════════════════════════════════════════════════════

CSS_PATCH = """
.fault-badge {
  font-size: 0.62rem;
  font-weight: bold;
  color: #f85149;
  background: rgba(248,81,73,0.12);
  border: 1px solid rgba(248,81,73,0.4);
  border-radius: 4px;
  padding: 2px 6px;
  margin-top: 4px;
  animation: fault-pulse 1s ease-in-out infinite alternate;
}
@keyframes fault-pulse {
  from { opacity: 1; }
  to   { opacity: 0.55; }
}
"""
