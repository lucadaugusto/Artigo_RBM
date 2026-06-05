# FIWARE Plant Simulation Stack

Arquitetura **Edge-to-Cloud** para integração entre o Siemens Tecnomatix Plant
Simulation e a plataforma FIWARE NGSI-v2, com persistência histórica via
STH-Comet e predição de falhas por Machine Learning (XGBoost).

```
Máquina local (Edge)                    AWS EC2 — Docker Compose
────────────────────                    ──────────────────────────────────────
Plant Simulation                        Mosquitto       :1883  (público)
  └─[TCP :9999]─► bridge.py ──[MQTT]──► IoT Agent MQTT :4041  (interno)
                   └─[SQLite]           Orion CB        :1026  (interno)
                                        MongoDB         :27017 (interno)
                                        STH-Comet       :8666  (interno)
                                        MongoDB-hist    :27017 (interno)
                                        ML Service      :5001  (interno)
                                        Dashboard/nginx :8080  (público)
```

---

## Índice

1. [Pré-requisitos](#1-pré-requisitos)
2. [Configuração da EC2 na AWS](#2-configuração-da-ec2-na-aws)
3. [Instalação automática (setup_ec2.sh)](#3-instalação-automática-setup_ec2sh)
4. [Instalação manual do Docker](#4-instalação-manual-do-docker)
5. [Estrutura de arquivos](#5-estrutura-de-arquivos)
6. [Subindo a stack](#6-subindo-a-stack)
7. [Provisionamento FIWARE](#7-provisionamento-fiware-executar-uma-vez)
8. [Smoke test](#8-smoke-test)
9. [Configuração da máquina edge (local)](#9-configuração-da-máquina-edge-local)
10. [Treino do modelo ML](#10-treino-do-modelo-ml)
11. [Deploy do modelo na EC2](#11-deploy-do-modelo-na-ec2)
12. [Acesso ao dashboard](#12-acesso-ao-dashboard)
13. [Referência de portas e endpoints](#13-referência-de-portas-e-endpoints)
14. [Payload MQTT — Ultralight 2.0](#14-payload-mqtt--ultralight-20)
15. [Operações do dia a dia](#15-operações-do-dia-a-dia)
16. [Diagnóstico e troubleshooting](#16-diagnóstico-e-troubleshooting)

---

## 1. Pré-requisitos

### Máquina edge (local — onde roda o Plant Simulation)

| Requisito       | Versão mínima                          |
|-----------------|----------------------------------------|
| Python          | 3.10+                                  |
| paho-mqtt       | 1.6+                                   |
| Plant Simulation| Student Edition ou licenciado          |

```bash
pip install paho-mqtt
```

Os arquivos `bridge.py` e `db.py` devem estar no mesmo diretório.

### AWS EC2

| Recurso    | Recomendado                        | Mínimo                        |
|------------|------------------------------------|-------------------------------|
| Instância  | **t3.medium** (2 vCPU / 4 GB RAM) | t3.small (2 vCPU / 2 GB RAM)  |
| Sistema    | Ubuntu 22.04 LTS                   | Ubuntu 20.04 LTS              |
| Disco      | 20 GB gp3                          | 15 GB                         |
| Docker     | 24+                                | 20+                           |
| Compose    | v2 (plugin)                        | v2                            |

> **Atenção:** `t3.micro` (1 GB RAM) **não suporta** a stack completa.
> O MongoDB sozinho consome ~300 MB; com todos os containers o pico fica em ~2,8 GB.

---

## 2. Configuração da EC2 na AWS

### 2.1 Criar a instância

No console AWS → EC2 → **Launch Instance**:

- **AMI:** Ubuntu Server 22.04 LTS (HVM), SSD Volume Type
- **Instance type:** `t3.medium`
- **Key pair:** crie ou selecione um par `.pem` existente
- **Storage:** 20 GiB gp3

### 2.2 Configurar o Security Group

Em **Network settings → Edit**, adicione as seguintes regras de **Inbound**:

| Tipo       | Protocolo | Porta | Origem                  | Motivo                        |
|------------|-----------|-------|-------------------------|-------------------------------|
| SSH        | TCP       | 22    | Seu IP                  | Acesso administrativo         |
| Custom TCP | TCP       | 1883  | IP da máquina edge      | MQTT — Plant Simulation       |
| Custom TCP | TCP       | 8080  | 0.0.0.0/0               | Dashboard web                 |

> **Não exponha** as portas 1026, 4041, 5001, 8666 e 27017 publicamente.
> O nginx as proxia internamente para o dashboard via rede Docker.

### 2.3 Conectar via SSH

```bash
chmod 400 sua-chave.pem
ssh -i sua-chave.pem ubuntu@<EC2_IP_PUBLICO>
```

---

## 3. Instalação automática (setup_ec2.sh)

O script `setup_ec2.sh` automatiza os passos 4 e 5 completos.
**Copie todos os arquivos do projeto para a EC2 antes de executá-lo:**

```bash
# Na sua máquina local — envia o projeto para a EC2:
scp -i sua-chave.pem -r fiware_stack/ ubuntu@<EC2_IP>:~/

# Na EC2 — executa o setup:
cd ~/fiware_stack
chmod +x setup_ec2.sh
./setup_ec2.sh
```

Após concluir, **aplique o grupo docker sem reiniciar a sessão:**

```bash
newgrp docker
```

Em seguida, vá direto para o [Passo 6](#6-subindo-a-stack).

---

## 4. Instalação manual do Docker

Se preferir instalar passo a passo, ou se o `setup_ec2.sh` não estiver disponível:

```bash
# 1. Atualiza o sistema
sudo apt-get update && sudo apt-get upgrade -y

# 2. Instala dependências base
sudo apt-get install -y ca-certificates curl gnupg lsb-release python3

# 3. Adiciona a chave GPG oficial do Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# 4. Adiciona o repositório oficial
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 5. Instala Docker Engine + Compose plugin
sudo apt-get update
sudo apt-get install -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

# 6. Habilita o serviço
sudo systemctl enable docker
sudo systemctl start docker

# 7. Adiciona o usuário ao grupo docker
sudo usermod -aG docker $USER
newgrp docker

# 8. Verifica
docker --version
docker compose version
```

### Tuning do kernel para MongoDB

```bash
# Evita warnings e degradação de performance no MongoDB
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
sudo sysctl -w vm.max_map_count=262144
```

---

## 5. Estrutura de arquivos

Certifique-se de que o projeto está organizado assim na EC2 antes de subir a stack:

```
~/fiware_stack/
├── docker-compose.yml
├── setup_ec2.sh
├── bridge.py                    ← usado apenas na máquina edge (local)
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
└── provisioning/
    └── provision.sh
```

Se os arquivos chegaram em uma pasta plana (sem subdiretórios), crie a estrutura:

```bash
cd ~/fiware_stack

mkdir -p mosquitto nginx dashboard ml_service provisioning

mv mosquitto.conf  mosquitto/
mv nginx.conf      nginx/
mv index.html      dashboard/
mv ml_service.py   ml_service/
mv Dockerfile      ml_service/
mv requirements.txt ml_service/
mv provision.sh    provisioning/

chmod +x provisioning/provision.sh
```

---

## 6. Subindo a stack

```bash
cd ~/fiware_stack

# Sobe todos os containers em background
docker compose up -d

# Acompanha os logs em tempo real (Ctrl+C para sair sem derrubar)
docker compose logs -f

# Verifica o status de saúde de cada container
docker compose ps
```

Aguarde todos os containers exibirem `healthy` antes de continuar.
Isso leva entre 45 e 90 segundos dependendo da velocidade de download das imagens.

**Saída esperada do `docker compose ps`:**

```
NAME           STATUS                   PORTS
mosquitto      Up X minutes (healthy)   0.0.0.0:1883->1883/tcp
mongodb        Up X minutes (healthy)
mongodb-hist   Up X minutes (healthy)
orion          Up X minutes (healthy)   127.0.0.1:1026->1026/tcp
iot-agent      Up X minutes (healthy)   127.0.0.1:4041->4041/tcp
sth-comet      Up X minutes (healthy)   127.0.0.1:8666->8666/tcp
ml-service     Up X minutes (healthy)   127.0.0.1:5001->5001/tcp
dashboard      Up X minutes (healthy)   0.0.0.0:8080->80/tcp
```

Se algum container ficar travado em `starting`, veja os logs dele:

```bash
docker compose logs orion
docker compose logs iot-agent
```

---

## 7. Provisionamento FIWARE (executar UMA VEZ)

> **Crítico:** execute este passo **depois** de todos os containers estarem
> `healthy` e **antes** de conectar o Plant Simulation. Mensagens MQTT que
> chegam antes do provisionamento são **descartadas silenciosamente** pelo
> IoT Agent — sem nenhum erro visível no log.

```bash
cd ~/fiware_stack

# Rodando no próprio host EC2 (padrão):
./provisioning/provision.sh

# Ou passando os endpoints explicitamente:
./provisioning/provision.sh http://localhost:4041 http://localhost:1026
```

O script executa automaticamente:

1. Aguarda IoT Agent e Orion ficarem disponíveis
2. Cria o **Service Group** com `apikey=plantbridge`
3. Registra os devices: `prensa-001`, `torno-001`, `buffer-001`
4. Cria subscription **Orion → STH-Comet** (persistência do histórico)
5. Cria subscription **Orion → ML Service** (feed de predição por push)
6. Imprime verificação final com devices e subscriptions registrados

**Saída esperada ao final:**

```
==> Verificando devices registrados ...
   Devices registrados: 3
   - prensa-001 -> urn:ngsi-v2:Machine:prensa-001
   - torno-001  -> urn:ngsi-v2:Machine:torno-001
   - buffer-001 -> urn:ngsi-v2:Inventory:buffer-001

==> Verificando subscriptions ...
   Subscriptions ativas: 2
   - Persist all machine context to STH-Comet [active]
   - Feed ML service on machine context update [active]

✅ Provisionamento concluído.
```

---

## 8. Smoke test

Execute esta sequência para validar toda a cadeia antes de ligar o Plant Simulation:

```bash
# 1. Publica uma mensagem de teste diretamente no broker
docker exec mosquitto mosquitto_pub \
  -h localhost -p 1883 \
  -t "/plantbridge/prensa-001/attrs" \
  -m "s=RUNNING|p=10|w=95.0|b=3.0|f=2.0"

# 2. Verifica se o Orion criou a entidade
curl -s "http://localhost:1026/v2/entities" \
  -H "fiware-service: factory" \
  -H "fiware-servicepath: /plant" | python3 -m json.tool
# Esperado: array com urn:ngsi-v2:Machine:prensa-001

# 3. Simula padrão pré-FAULT (BLOCKED crescente) para testar o ML
for i in 1 2 3 4 5; do
  docker exec mosquitto mosquitto_pub \
    -h localhost -p 1883 \
    -t "/plantbridge/prensa-001/attrs" \
    -m "s=BLOCKED|p=$((10+i))|w=55.0|b=35.0|f=3.0"
  sleep 1
done

# 4. Consulta a predição do ML Service
curl -s http://localhost:5001/last_predictions | python3 -m json.tool
# Esperado: fault_probability > 0 para prensa-001

# 5. Verifica o histórico no STH-Comet
curl -s \
  "http://localhost:8666/STH/v1/contextEntities/type/Machine/\
id/urn:ngsi-v2:Machine:prensa-001/attributes/status?lastN=5" \
  -H "fiware-service: factory" \
  -H "fiware-servicepath: /plant" | python3 -m json.tool
# Esperado: array com os últimos 5 valores de status
```

---

## 9. Configuração da máquina edge (local)

O `bridge.py` roda **na máquina local** (onde está o Plant Simulation) e faz a
ponte TCP → MQTT entre o simulador e a EC2.

```bash
# Na máquina local — instala a dependência
pip install paho-mqtt

# Executa o bridge apontando para o IP público da EC2
python bridge.py --host <EC2_IP_PUBLICO> --mqtt-port 1883

# Argumentos disponíveis:
#   --host       IP ou hostname da EC2       (padrão: localhost)
#   --mqtt-port  porta MQTT na EC2           (padrão: 1883)
#   --tcp-port   porta TCP local para o PS   (padrão: 9999)
```

No **Plant Simulation**, configure o objeto `Socket` para conectar em
`localhost:9999`. O bridge converte o JSON recebido para Ultralight 2.0 e
publica via MQTT na EC2 automaticamente.

---

## 10. Treino do modelo ML

O modelo é treinado **offline, na máquina local**, após acumular dados de
simulação no SQLite (`events.db`), que é populado automaticamente pelo bridge.

### 10.1 Verificar dados acumulados

```bash
python3 -c "
from db import count_snapshots
for d in ['prensa-001', 'torno-001']:
    print(f'{d}: {count_snapshots(d)} snapshots')
"
```

Execute pelo menos **3 a 5 sessões completas** do Plant Simulation para
acumular dados suficientes, incluindo eventos de FAULT.

### 10.2 Executar o treino

```bash
# Treino para a prensa (mínimo 100 snapshots):
python train.py --device prensa-001 --window 5 --min-rows 100

# Treino para o torno:
python train.py --device torno-001 --window 5 --min-rows 100
```

**Saída esperada:**

```
[train] Snapshots encontrados para 'prensa-001': 420
[train] Dataset: 415 amostras | FAULT=28 (6.7%)

── Classification Report ─────────────────────────────
              precision    recall  f1-score   support
      Normal       0.98      0.99      0.98        74
       FAULT       0.93      0.86      0.89         7

ROC-AUC: 0.9714

[train] Modelo salvo em: model.pkl
[train] Features salvas em: feature_columns.json
```

---

## 11. Deploy do modelo na EC2

```bash
# Na máquina local — envia os arquivos para a EC2
scp -i sua-chave.pem model.pkl feature_columns.json \
  ubuntu@<EC2_IP>:/tmp/

# Na EC2 — copia para dentro do volume Docker do container
docker cp /tmp/model.pkl            ml-service:/app/models/model.pkl
docker cp /tmp/feature_columns.json ml-service:/app/models/feature_columns.json

# Recarrega o modelo sem reiniciar o container
curl -s -X POST http://localhost:5001/reload

# Confirma que o modelo foi carregado
curl -s http://localhost:5001/health
```

**Resposta esperada do `/health`:**

```json
{
  "status": "ok",
  "model_loaded": true,
  "window": 5,
  "threshold": 0.6,
  "devices_buffered": []
}
```

---

## 12. Acesso ao dashboard

```
http://<EC2_IP_PUBLICO>:8080
```

O dashboard consulta automaticamente, via nginx proxy:

| Rota interna   | Destino              | Intervalo   |
|----------------|----------------------|-------------|
| `/orion/`      | Orion CB :1026       | 3 segundos  |
| `/sth/`        | STH-Comet :8666      | 15 segundos |
| `/ml/`         | ML Service :5001     | 3 segundos  |

---

## 13. Referência de portas e endpoints

### Portas na EC2

| Porta  | Serviço          | Acesso externo                    |
|--------|------------------|-----------------------------------|
| 1883   | Mosquitto MQTT   | Sim — restrito ao IP edge (SG)    |
| 8080   | Dashboard nginx  | Sim — público                     |
| 1026   | Orion CB         | Não — somente localhost           |
| 4041   | IoT Agent        | Não — somente localhost           |
| 5001   | ML Service       | Não — somente localhost           |
| 8666   | STH-Comet        | Não — somente localhost           |
| 27017  | MongoDB (×2)     | Não — somente containers          |

### Endpoints de diagnóstico (a partir da EC2)

```bash
# Versão do Orion
curl -s http://localhost:1026/version

# Todas as entidades NGSI-v2
curl -s "http://localhost:1026/v2/entities" \
  -H "fiware-service: factory" -H "fiware-servicepath: /plant"

# Subscriptions ativas
curl -s "http://localhost:1026/v2/subscriptions" \
  -H "fiware-service: factory" -H "fiware-servicepath: /plant"

# Devices registrados no IoT Agent
curl -s "http://localhost:4041/iot/devices" \
  -H "fiware-service: factory" -H "fiware-servicepath: /plant"

# Histórico STH-Comet (últimos 10 valores de status)
curl -s "http://localhost:8666/STH/v1/contextEntities/type/Machine/\
id/urn:ngsi-v2:Machine:prensa-001/attributes/status?lastN=10" \
  -H "fiware-service: factory" -H "fiware-servicepath: /plant"

# Saúde do ML Service
curl -s http://localhost:5001/health

# Últimas predições de FAULT
curl -s http://localhost:5001/last_predictions
```

---

## 14. Payload MQTT — Ultralight 2.0

O IoT Agent **rejeita JSON** no tópico `/attrs`. O formato obrigatório é
**Ultralight 2.0**: pares `chave=valor` separados por `|`.

| Atributo NGSI-v2  | object_id | Tipo    | Exemplo       |
|-------------------|-----------|---------|---------------|
| status            | s         | Text    | `s=RUNNING`   |
| productionCount   | p         | Integer | `p=628`       |
| workingPct        | w         | Number  | `w=65.5`      |
| blockedPct        | b         | Number  | `b=23.7`      |
| failedPct         | f         | Number  | `f=10.8`      |
| occupancy (buffer)| o         | Integer | `o=7`         |

**Payload completo (máquina):**

```
s=RUNNING|p=628|w=65.5|b=23.7|f=10.8
```

**Tópicos por device:**

```
/plantbridge/prensa-001/attrs
/plantbridge/torno-001/attrs
/plantbridge/buffer-001/attrs
```

---

## 15. Operações do dia a dia

### Iniciar / parar a stack

```bash
cd ~/fiware_stack

docker compose up -d          # sobe tudo em background
docker compose down           # para e remove os containers (volumes preservados)
docker compose down -v        # para e APAGA os volumes (dados perdidos)
docker compose restart orion  # reinicia um serviço específico
```

### Ver logs

```bash
docker compose logs -f               # todos os serviços
docker compose logs -f orion         # apenas o Orion
docker compose logs --tail=50 iot-agent  # últimas 50 linhas do IoT Agent
```

### Monitorar recursos

```bash
docker stats --no-stream   # snapshot de CPU/RAM por container
docker compose ps          # status e health de todos os serviços
```

### Atualizar o modelo ML sem parar a stack

```bash
# 1. Treinar na máquina edge e enviar
scp -i sua-chave.pem model.pkl feature_columns.json ubuntu@<EC2_IP>:/tmp/

# 2. Copiar para o volume e recarregar
docker cp /tmp/model.pkl            ml-service:/app/models/model.pkl
docker cp /tmp/feature_columns.json ml-service:/app/models/feature_columns.json
curl -s -X POST http://localhost:5001/reload
```

### Recriar apenas o ML Service (após mudança no código)

```bash
docker compose up -d --build ml-service
```

---

## 16. Diagnóstico e troubleshooting

### Container não sai do estado `starting`

```bash
# Ver logs detalhados
docker compose logs --tail=50 <nome-do-container>

# Reiniciar o serviço
docker compose restart <nome-do-container>

# Forçar recriação (sem apagar volumes)
docker compose up -d --force-recreate <nome-do-container>
```

### Mensagem MQTT chega mas o Orion não atualiza

Causa mais comum: device não provisionado ou API key incorreta.

```bash
# Verifica se o device existe no IoT Agent
curl -s "http://localhost:4041/iot/devices/prensa-001" \
  -H "fiware-service: factory" \
  -H "fiware-servicepath: /plant"
# Se retornar 404, execute o provision.sh novamente

# Acompanha o IoT Agent em tempo real
docker compose logs -f iot-agent
```

### Orion não notifica o STH-Comet ou o ML Service

```bash
# Verifica o status das subscriptions
curl -s "http://localhost:1026/v2/subscriptions" \
  -H "fiware-service: factory" \
  -H "fiware-servicepath: /plant" | python3 -m json.tool
# Procure pelo campo "status": deve ser "active"
# "failed" indica que o endpoint de notificação não respondeu
```

### ML Service retorna `"model_loaded": false`

O `model.pkl` não foi copiado para o volume ainda. Siga o [Passo 11](#11-deploy-do-modelo-na-ec2).

```bash
# Verifica se o arquivo existe dentro do container
docker exec ml-service ls /app/models/
```

### Dashboard não carrega dados (502 Bad Gateway)

```bash
# Testa o proxy nginx → Orion
curl -s http://localhost:8080/orion/v2/version

# Se retornar 502, o Orion pode ainda não estar healthy
docker compose ps orion
```

### Consumo de memória próximo do limite

```bash
docker stats --no-stream

# Se o mongodb ou orion estiverem acima de 1,5 GB, considere
# fazer upgrade para t3.large (8 GB RAM)
```

### Reprovisionar do zero (sem apagar dados)

```bash
# Remove subscriptions e devices existentes
curl -s "http://localhost:1026/v2/subscriptions" \
  -H "fiware-service: factory" -H "fiware-servicepath: /plant" \
  | python3 -c "
import json, sys, subprocess
for s in json.load(sys.stdin):
    subprocess.run(['curl','-s','-X','DELETE',
      f'http://localhost:1026/v2/subscriptions/{s[\"id\"]}',
      '-H','fiware-service: factory',
      '-H','fiware-servicepath: /plant'])
    print(f'Removida: {s[\"id\"]}')
"

# Executa o provision.sh novamente
./provisioning/provision.sh
```

---

## Sequência resumida de instalação

```
EC2:
  1.  Criar instância t3.medium + Security Group (portas 22, 1883, 8080)
  2.  ssh ubuntu@<EC2_IP>
  3.  scp -r fiware_stack/ ubuntu@<EC2_IP>:~/
  4.  ./setup_ec2.sh          (ou instalação manual — Passo 4)
  5.  newgrp docker
  6.  cd ~/fiware_stack
  7.  docker compose up -d
  8.  docker compose ps       (aguarda todos healthy)
  9.  ./provisioning/provision.sh

Edge (máquina local):
  10. pip install paho-mqtt
  11. python bridge.py --host <EC2_IP>
  12. Conectar Plant Simulation → TCP localhost:9999

  (após 3–5 sessões de simulação):
  13. python train.py --device prensa-001
  14. scp model.pkl feature_columns.json ubuntu@<EC2_IP>:/tmp/

EC2:
  15. docker cp /tmp/model.pkl ml-service:/app/models/model.pkl
  16. docker cp /tmp/feature_columns.json ml-service:/app/models/feature_columns.json
  17. curl -X POST http://localhost:5001/reload

Navegador:
  18. http://<EC2_IP>:8080
```
