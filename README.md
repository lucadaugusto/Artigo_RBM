# Deploy Guide — FIWARE Plant Simulation Stack

## Estrutura de arquivos

```
fiware_stack/
├── docker-compose.yml
├── mosquitto/
│   └── mosquitto.conf
├── nginx/
│   └── nginx.conf
├── dashboard/
│   └── index.html
├── ml_service/
│   ├── ml_service.py
│   ├── Dockerfile
│   └── requirements.txt
├── provisioning/
│   └── provision.sh
└── bridge.py              ← roda na máquina edge (local)
```

---

## 1. Pré-requisitos na EC2

```bash
# Instância recomendada: t3.medium (2 vCPU, 4 GB RAM), Ubuntu 22.04
sudo apt update && sudo apt install -y docker.io docker-compose-plugin curl
sudo usermod -aG docker $USER && newgrp docker
```

**Security Group (portas a abrir):**
| Porta | Protocolo | Origem         | Motivo                     |
|-------|-----------|----------------|----------------------------|
| 1883  | TCP       | IP da máquina edge | MQTT (Plant Simulation) |
| 8080  | TCP       | 0.0.0.0/0      | Dashboard público           |
| 22    | TCP       | Seu IP         | SSH                        |

> Portas 1026, 4041, 5001, 8666 NÃO devem ser expostas publicamente.
> O nginx as proxia internamente para o dashboard.

---

## 2. Deploy na EC2

```bash
# Sobe toda a stack
docker compose up -d

# Acompanha os logs
docker compose logs -f

# Verifica saúde dos containers
docker compose ps
```

---

## 3. Provisionamento FIWARE (executar UMA VEZ)

```bash
chmod +x provisioning/provision.sh

# Rodando no próprio host EC2:
./provisioning/provision.sh http://localhost:4041 http://localhost:1026

# Rodando de outra máquina (substitua o IP):
./provisioning/provision.sh http://1.2.3.4:4041 http://1.2.3.4:1026
```

O script registra:
- Service Group (apikey=`plantbridge`)
- Devices: `prensa-001`, `torno-001`, `buffer-001`
- Subscription Orion → STH-Comet (histórico)
- Subscription Orion → ML Service (predição)

---

## 4. Deploy do modelo ML

O `model.pkl` é gerado offline pela máquina edge após acumular dados.
Copie para o volume Docker da EC2:

```bash
# Na máquina edge, após rodar train.py:
scp model.pkl feature_columns.json ubuntu@<EC2_IP>:/tmp/

# Na EC2:
docker cp /tmp/model.pkl   ml-service:/app/models/model.pkl
docker cp /tmp/feature_columns.json ml-service:/app/models/feature_columns.json

# Recarrega sem reiniciar o container:
curl -X POST http://localhost:5001/reload
```

---

## 5. Configuração da máquina edge (local)

```bash
# Instala dependências
pip install paho-mqtt

# Copia bridge.py e db.py para a máquina local
# Executa apontando para o IP público da EC2:
python bridge.py --host <EC2_IP> --mqtt-port 1883
```

O Plant Simulation continua enviando para `localhost:9999` como antes.
O `bridge.py` substitui o papel do `app.py` para a comunicação MQTT.

---

## 6. Teste de fumaça (smoke test)

```bash
# Na EC2 — publica mensagem de teste manualmente:
docker exec mosquitto mosquitto_pub \
  -h localhost -p 1883 \
  -t "/plantbridge/prensa-001/attrs" \
  -m "s=RUNNING|p=10|w=95.0|b=3.0|f=2.0"

# Verifica se o Orion recebeu:
curl -s http://localhost:1026/v2/entities \
  -H "fiware-service: factory" \
  -H "fiware-servicepath: /plant" | python3 -m json.tool

# Dashboard:
# http://<EC2_IP>:8080
```

---

## 7. Sequência de fluxo de dados

```
Plant Simulation
  └─[TCP :9999]→ bridge.py (edge)
                  ├─[SQLite]→ buffer local para treino ML
                  └─[MQTT /plantbridge/<id>/attrs]→ Mosquitto (EC2 :1883)
                                                      └→ IoT Agent MQTT
                                                          └→ Orion CB :1026
                                                              ├─[persist]→ MongoDB
                                                              ├─[subscription]→ STH-Comet :8666
                                                              │               └→ MongoDB-hist
                                                              └─[subscription]→ ML Service :5001
                                                                              └─[/last_predictions]→ Dashboard :8080
```

---

## 8. Tópico MQTT e payload Ultralight

| Campo      | object_id | Exemplo         |
|------------|-----------|-----------------|
| status     | s         | `s=RUNNING`     |
| production | p         | `p=628`         |
| working %  | w         | `w=65.5`        |
| blocked %  | b         | `b=23.7`        |
| failed %   | f         | `f=10.8`        |

Payload completo: `s=RUNNING|p=628|w=65.5|b=23.7|f=10.8`
Tópico: `/plantbridge/prensa-001/attrs`

---

## 9. Consultas úteis de diagnóstico

```bash
# Lista entities no Orion
curl -s "http://localhost:1026/v2/entities?type=Machine" \
  -H "fiware-service: factory" -H "fiware-servicepath: /plant"

# Histórico STH-Comet (últimos 10 valores de status da prensa)
curl -s "http://localhost:8666/STH/v1/contextEntities/type/Machine/id/urn:ngsi-v2:Machine:prensa-001/attributes/status?lastN=10" \
  -H "fiware-service: factory" -H "fiware-servicepath: /plant"

# Subscriptions ativas no Orion
curl -s "http://localhost:1026/v2/subscriptions" \
  -H "fiware-service: factory" -H "fiware-servicepath: /plant"

# Saúde do ML Service
curl -s http://localhost:5001/health

# Últimas predições
curl -s http://localhost:5001/last_predictions
```
