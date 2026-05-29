#!/usr/bin/env bash
# provision.sh — Provisionamento FIWARE NGSI-v2
#
# Executa UMA VEZ após o docker-compose up para registrar:
#   1. Service Group (mapeia a API key MQTT para o serviço)
#   2. Devices: prensa-001, torno-001, buffer-001
#
# Após provisionar, o IoT Agent aceita mensagens no tópico:
#   /<APIKEY>/<device_id>/attrs
#
# Uso:
#   chmod +x provision.sh
#   ./provision.sh                        # roda contra localhost
#   ./provision.sh http://1.2.3.4:4041   # roda contra EC2 remota

set -euo pipefail

IOTA_URL="${1:-http://localhost:4041}"
ORION_URL="${2:-http://localhost:1026}"
FIWARE_SERVICE="factory"
FIWARE_SERVICEPATH="/plant"
APIKEY="plantbridge"   # deve coincidir com o python bridge

echo "==> Aguardando IoT Agent em $IOTA_URL ..."
until curl -sf "$IOTA_URL/iot/about" > /dev/null; do
    printf '.'
    sleep 2
done
echo " OK"

echo "==> Aguardando Orion em $ORION_URL ..."
until curl -sf "$ORION_URL/version" > /dev/null; do
    printf '.'
    sleep 2
done
echo " OK"

# ── 1. Service Group ──────────────────────────────────────────────────────────
# O service group vincula a API key MQTT ao serviço FIWARE.
# Todos os devices que publicarem com esta API key herdam o serviço/path.
echo ""
echo "==> Registrando Service Group (apikey=$APIKEY) ..."
curl -sf -X POST "$IOTA_URL/iot/services" \
  -H "Content-Type: application/json" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH" \
  -d '{
    "services": [{
      "apikey":            "'"$APIKEY"'",
      "cbroker":           "'"$ORION_URL"'",
      "entity_type":       "Machine",
      "resource":          "/iot/d",
      "transport":         "MQTT",
      "protocol":          "IoTA-UL",
      "attributes": [
        { "object_id": "s",  "name": "status",         "type": "Text"    },
        { "object_id": "p",  "name": "productionCount", "type": "Integer" },
        { "object_id": "w",  "name": "workingPct",      "type": "Number"  },
        { "object_id": "b",  "name": "blockedPct",      "type": "Number"  },
        { "object_id": "f",  "name": "failedPct",       "type": "Number"  }
      ],
      "static_attributes": [
        { "name": "dataProvider", "type": "Text", "value": "PlantSimulation" }
      ]
    }]
  }' && echo " OK" || echo " (já existe ou erro — verifique manualmente)"

# ── 2. Device: prensa-001 ─────────────────────────────────────────────────────
echo ""
echo "==> Registrando device prensa-001 ..."
curl -sf -X POST "$IOTA_URL/iot/devices" \
  -H "Content-Type: application/json" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH" \
  -d '{
    "devices": [{
      "device_id":   "prensa-001",
      "entity_name": "urn:ngsi-v2:Machine:prensa-001",
      "entity_type": "Machine",
      "transport":   "MQTT",
      "protocol":    "IoTA-UL",
      "attributes": [
        { "object_id": "s",  "name": "status",         "type": "Text"    },
        { "object_id": "p",  "name": "productionCount", "type": "Integer" },
        { "object_id": "w",  "name": "workingPct",      "type": "Number"  },
        { "object_id": "b",  "name": "blockedPct",      "type": "Number"  },
        { "object_id": "f",  "name": "failedPct",       "type": "Number"  }
      ],
      "static_attributes": [
        { "name": "machineType", "type": "Text", "value": "press" }
      ]
    }]
  }' && echo " OK" || echo " (já existe)"

# ── 3. Device: torno-001 ─────────────────────────────────────────────────────
echo ""
echo "==> Registrando device torno-001 ..."
curl -sf -X POST "$IOTA_URL/iot/devices" \
  -H "Content-Type: application/json" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH" \
  -d '{
    "devices": [{
      "device_id":   "torno-001",
      "entity_name": "urn:ngsi-v2:Machine:torno-001",
      "entity_type": "Machine",
      "transport":   "MQTT",
      "protocol":    "IoTA-UL",
      "attributes": [
        { "object_id": "s",  "name": "status",         "type": "Text"    },
        { "object_id": "p",  "name": "productionCount", "type": "Integer" },
        { "object_id": "w",  "name": "workingPct",      "type": "Number"  },
        { "object_id": "b",  "name": "blockedPct",      "type": "Number"  },
        { "object_id": "f",  "name": "failedPct",       "type": "Number"  }
      ],
      "static_attributes": [
        { "name": "machineType", "type": "Text", "value": "lathe" }
      ]
    }]
  }' && echo " OK" || echo " (já existe)"

# ── 4. Device: buffer-001 ────────────────────────────────────────────────────
echo ""
echo "==> Registrando device buffer-001 ..."
curl -sf -X POST "$IOTA_URL/iot/devices" \
  -H "Content-Type: application/json" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH" \
  -d '{
    "devices": [{
      "device_id":   "buffer-001",
      "entity_name": "urn:ngsi-v2:Inventory:buffer-001",
      "entity_type": "Inventory",
      "transport":   "MQTT",
      "protocol":    "IoTA-UL",
      "attributes": [
        { "object_id": "o", "name": "occupancy", "type": "Integer" },
        { "object_id": "c", "name": "capacity",  "type": "Integer" }
      ],
      "static_attributes": [
        { "name": "bufferType", "type": "Text", "value": "queue" }
      ]
    }]
  }' && echo " OK" || echo " (já existe)"

# ── 5. Subscription: Orion → STH-Comet ───────────────────────────────────────
# Toda atualização de contexto dispara notificação HTTP para o STH-Comet,
# que armazena a série temporal automaticamente.
echo ""
echo "==> Criando subscription Orion → STH-Comet ..."
curl -sf -X POST "$ORION_URL/v2/subscriptions" \
  -H "Content-Type: application/json" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH" \
  -d '{
    "description": "Persist all machine context to STH-Comet",
    "subject": {
      "entities": [
        { "idPattern": ".*", "type": "Machine"   },
        { "idPattern": ".*", "type": "Inventory" }
      ],
      "condition": { "attrs": [] }
    },
    "notification": {
      "http": { "url": "http://sth-comet:8666/notify" },
      "attrs": [],
      "attrsFormat": "legacy"
    },
    "throttling": 1
  }' && echo " OK" || echo " (erro — verifique manualmente)"

# ── 6. Subscription: Orion → ML Service ──────────────────────────────────────
# A cada atualização de contexto de Machine, o Orion notifica o ML Service.
# O ML Service recebe o payload, atualiza o buffer e recalcula a predição.
echo ""
echo "==> Criando subscription Orion → ML Service ..."
curl -sf -X POST "$ORION_URL/v2/subscriptions" \
  -H "Content-Type: application/json" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH" \
  -d '{
    "description": "Feed ML service on machine context update",
    "subject": {
      "entities": [{ "idPattern": ".*", "type": "Machine" }],
      "condition": { "attrs": ["status", "workingPct", "blockedPct", "failedPct"] }
    },
    "notification": {
      "http": { "url": "http://ml-service:5001/notify" },
      "attrs": ["status", "productionCount", "workingPct", "blockedPct", "failedPct"],
      "attrsFormat": "normalized"
    },
    "throttling": 1
  }' && echo " OK" || echo " (erro — verifique manualmente)"

# ── Verificação final ─────────────────────────────────────────────────────────
echo ""
echo "==> Verificando devices registrados ..."
curl -s "$IOTA_URL/iot/devices" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH" | python3 -c "
import json,sys
data = json.load(sys.stdin)
devices = data.get('devices', [])
print(f'   Devices registrados: {len(devices)}')
for d in devices:
    print(f'   - {d[\"device_id\"]} -> {d[\"entity_name\"]}')
"

echo ""
echo "==> Verificando subscriptions ..."
curl -s "$ORION_URL/v2/subscriptions" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH" | python3 -c "
import json,sys
subs = json.load(sys.stdin)
print(f'   Subscriptions ativas: {len(subs)}')
for s in subs:
    print(f'   - {s[\"description\"]} [{s[\"status\"]}]')
"

echo ""
echo "✅ Provisionamento concluído."
echo ""
echo "Tópico MQTT esperado pelo IoT Agent (formato Ultralight):"
echo "  Topic : /$APIKEY/<device_id>/attrs"
echo "  Payload: s=RUNNING|p=628|w=65.5|b=23.7|f=10.8"
echo ""
echo "Exemplo de teste manual (do host EC2):"
echo "  mosquitto_pub -h localhost -p 1883 \\"
echo "    -t '/$APIKEY/prensa-001/attrs' \\"
echo "    -m 's=RUNNING|p=10|w=95.0|b=3.0|f=2.0'"
