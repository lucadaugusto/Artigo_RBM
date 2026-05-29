"""
bridge.py — Edge bridge: TCP (Plant Simulation) → MQTT Ultralight (FIWARE IoT Agent)

Fluxo:
  Plant Simulation → TCP :9999 → bridge → MQTT /<apikey>/<device_id>/attrs
                                        → SQLite (buffer local para ML)

Formato de entrada (JSON via TCP, compatível com o app.py existente):
  [
    {"device_id":"prensa-001","status":"RUNNING","producao":628,
     "working":65.5,"blocked":23.71,"failed":10.79},
    {"device_id":"torno-001","status":"RUNNING","producao":618,
     "working":96.69,"blocked":0,"failed":3.2},
    {"device_id":"buffer-001","ocupacao":9}
  ]

Formato de saída MQTT (Ultralight 2.0 — exigido pelo IoT Agent):
  Tópico : /plantbridge/prensa-001/attrs
  Payload: s=RUNNING|p=628|w=65.5|b=23.71|f=10.79

Executar:
  python bridge.py
  python bridge.py --host ec2-xx-xx-xx-xx.compute.amazonaws.com --mqtt-port 1883
"""

import argparse
import json
import logging
import socket
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

# Importa camada de persistência local (mesmo diretório)
import sys
sys.path.insert(0, str(Path(__file__).parent))
from db import init_db, insert_snapshot

# ── Configuração ──────────────────────────────────────────────────────────────
TCP_HOST      = "0.0.0.0"
TCP_PORT      = 9999
MQTT_APIKEY   = "plantbridge"          # deve coincidir com provision.sh
FIWARE_SERVICE     = "factory"
FIWARE_SERVICEPATH = "/plant"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── MQTT client ───────────────────────────────────────────────────────────────

def build_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(client_id="plant-bridge", protocol=mqtt.MQTTv311)
    client.on_connect    = lambda c, u, f, rc: log.info(f"MQTT conectado (rc={rc})")
    client.on_disconnect = lambda c, u, rc:    log.warning(f"MQTT desconectado (rc={rc})")
    return client


def ul_payload_machine(item: dict) -> str:
    """
    Converte dict de máquina para payload Ultralight 2.0.
    Mapeamento de nomes curtos (object_id) conforme provision.sh:
      s = status | p = productionCount | w = workingPct | b = blockedPct | f = failedPct
    """
    parts = [f"s={item.get('status','IDLE')}",
             f"p={item.get('producao', 0)}"]
    if item.get("working") is not None:
        parts.append(f"w={item['working']}")
    if item.get("blocked") is not None:
        parts.append(f"b={item['blocked']}")
    if item.get("failed") is not None:
        parts.append(f"f={item['failed']}")
    return "|".join(parts)


def ul_payload_buffer(item: dict) -> str:
    """Payload Ultralight para o buffer/inventário."""
    return f"o={item.get('ocupacao',0)}|c=10"


# ── Processamento de mensagens ────────────────────────────────────────────────

_last_producao: dict[str, int] = {}


def process_batch(items: list[dict], mqtt_client: mqtt.Client) -> None:
    """Processa um batch JSON do Plant Simulation, publica via MQTT e persiste."""
    for item in items:
        device_id = item.get("device_id")
        if not device_id:
            continue

        topic = f"/{MQTT_APIKEY}/{device_id}/attrs"

        if device_id == "buffer-001":
            payload = ul_payload_buffer(item)
            mqtt_client.publish(topic, payload, qos=1)
            log.debug(f"MQTT pub [{topic}] {payload}")
            continue

        payload = ul_payload_machine(item)
        result  = mqtt_client.publish(topic, payload, qos=1)

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            log.error(f"Falha ao publicar {device_id}: rc={result.rc}")
        else:
            log.info(f"pub {device_id} | {payload}")

        # Persiste localmente para o ML Service
        prev     = _last_producao.get(device_id)
        producao = int(item.get("producao", 0))
        delta    = (producao - prev) if prev is not None else 0
        _last_producao[device_id] = producao

        insert_snapshot(
            device_id = device_id,
            status    = item.get("status", "IDLE"),
            producao  = producao,
            delta     = delta,
            working   = item.get("working"),
            blocked   = item.get("blocked"),
            failed    = item.get("failed"),
        )


# ── TCP Server ────────────────────────────────────────────────────────────────

def handle_client(conn: socket.socket, addr: tuple, mqtt_client: mqtt.Client) -> None:
    """Trata uma conexão TCP do Plant Simulation."""
    log.info(f"Conexão TCP de {addr}")
    buffer = ""
    try:
        while True:
            chunk = conn.recv(4096).decode("utf-8", errors="replace")
            if not chunk:
                break
            buffer += chunk
            # Tenta extrair JSON completo (array ou objeto)
            try:
                data = json.loads(buffer.strip())
                buffer = ""
                if isinstance(data, list):
                    process_batch(data, mqtt_client)
                elif isinstance(data, dict):
                    process_batch([data], mqtt_client)
            except json.JSONDecodeError:
                pass   # aguarda mais dados
    except Exception as e:
        log.error(f"Erro no handler TCP: {e}")
    finally:
        conn.close()
        log.info(f"Conexão TCP {addr} encerrada")


def run_tcp_server(mqtt_client: mqtt.Client, tcp_port: int) -> None:
    """Servidor TCP que aceita conexões do Plant Simulation."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((TCP_HOST, tcp_port))
    srv.listen(5)
    log.info(f"TCP escutando em {TCP_HOST}:{tcp_port}")
    while True:
        conn, addr = srv.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr, mqtt_client),
                             daemon=True)
        t.start()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Edge bridge — Plant Simulation → FIWARE")
    parser.add_argument("--host",      default="localhost",
                        help="IP/hostname do broker MQTT na EC2")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--tcp-port",  type=int, default=TCP_PORT)
    args = parser.parse_args()

    init_db()
    log.info("SQLite inicializado")

    client = build_mqtt_client()
    log.info(f"Conectando ao broker MQTT {args.host}:{args.mqtt_port} ...")
    client.connect(args.host, args.mqtt_port, keepalive=60)
    client.loop_start()

    # Aguarda conexão MQTT antes de abrir TCP
    time.sleep(2)

    run_tcp_server(client, args.tcp_port)


if __name__ == "__main__":
    main()
