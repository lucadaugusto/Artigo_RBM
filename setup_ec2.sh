#!/usr/bin/env bash
# =============================================================================
# setup_ec2.sh — Instalação completa da stack FIWARE no Ubuntu Server (EC2)
#
# O que este script faz:
#   1. Atualiza o sistema
#   2. Instala Docker Engine + Docker Compose plugin (método oficial)
#   3. Configura o usuário ubuntu no grupo docker (sem sudo)
#   4. Cria a estrutura de diretórios do projeto
#   5. Verifica dependências obrigatórias (curl, python3)
#   6. Testa a instalação com docker run hello-world
#   7. Imprime o checklist do que fazer depois
#
# Uso:
#   chmod +x setup_ec2.sh
#   ./setup_ec2.sh
#
# Testado em: Ubuntu 22.04 LTS e Ubuntu 24.04 LTS (AWS EC2 t3.medium)
# =============================================================================

set -euo pipefail

# ── Cores para output ─────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log_ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
log_info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_step() { echo -e "\n${BOLD}${CYAN}══ $* ══${NC}"; }
log_err()  { echo -e "${RED}[ERRO]${NC}  $*" >&2; }

# ── Verificação: deve rodar como usuário não-root com sudo ────────────────────
if [[ $EUID -eq 0 ]]; then
  log_err "Não execute como root. Use: ./setup_ec2.sh (como ubuntu)"
  exit 1
fi

# ── Variáveis do projeto ──────────────────────────────────────────────────────
PROJECT_DIR="${HOME}/fiware_stack"
UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "unknown")
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════════════════╗"
echo "║   FIWARE Plant Simulation Stack — Setup EC2          ║"
echo "║   Ubuntu ${UBUNTU_VERSION} · ${ARCH}                          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"

# =============================================================================
# PASSO 1 — Atualização do sistema
# =============================================================================
log_step "PASSO 1: Atualização do sistema"

sudo apt-get update -qq
sudo apt-get upgrade -y -qq
log_ok "Sistema atualizado"

# =============================================================================
# PASSO 2 — Dependências base
# =============================================================================
log_step "PASSO 2: Dependências base"

sudo apt-get install -y -qq \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  python3 \
  python3-pip \
  git \
  unzip \
  htop \
  net-tools \
  ufw

log_ok "Pacotes base instalados"

# =============================================================================
# PASSO 3 — Docker Engine + Compose plugin (método oficial Docker Inc.)
# =============================================================================
log_step "PASSO 3: Docker Engine e Docker Compose plugin"

# Remove versões antigas se existirem
log_info "Removendo versões antigas do Docker (se houver)..."
sudo apt-get remove -y -qq \
  docker docker-engine docker.io containerd runc \
  docker-compose docker-compose-plugin \
  2>/dev/null || true

# Adiciona a chave GPG oficial do Docker
log_info "Adicionando chave GPG do Docker..."
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Adiciona o repositório oficial
log_info "Adicionando repositório Docker..."
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Instala Docker Engine + Compose plugin
sudo apt-get update -qq
sudo apt-get install -y -qq \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

# Inicia e habilita o serviço
sudo systemctl enable docker
sudo systemctl start docker

# Adiciona o usuário atual ao grupo docker
sudo usermod -aG docker "$USER"

log_ok "Docker Engine instalado: $(docker --version)"
log_ok "Docker Compose instalado: $(docker compose version)"

# =============================================================================
# PASSO 4 — Verificação funcional do Docker
# =============================================================================
log_step "PASSO 4: Verificação funcional"

log_info "Testando docker com hello-world (via sudo — grupo ainda não ativo)..."
if sudo docker run --rm hello-world > /dev/null 2>&1; then
  log_ok "Docker funcional"
else
  log_err "Falha no teste do Docker. Verifique: sudo systemctl status docker"
  exit 1
fi

# =============================================================================
# PASSO 5 — Estrutura de diretórios do projeto
# =============================================================================
log_step "PASSO 5: Estrutura de diretórios do projeto"

mkdir -p "${PROJECT_DIR}"/{mosquitto,nginx,dashboard,ml_service,provisioning}

log_ok "Estrutura criada em ${PROJECT_DIR}:"
log_info "  ${PROJECT_DIR}/"
log_info "  ├── mosquitto/       ← mosquitto.conf"
log_info "  ├── nginx/           ← nginx.conf"
log_info "  ├── dashboard/       ← index.html"
log_info "  ├── ml_service/      ← ml_service.py · Dockerfile · requirements.txt"
log_info "  └── provisioning/    ← provision.sh"

# =============================================================================
# PASSO 6 — Copia arquivos já presentes no diretório atual para os destinos
# =============================================================================
log_step "PASSO 6: Posicionando arquivos do projeto"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Função de cópia com verificação
copy_if_exists() {
  local src="$1" dst="$2" label="$3"
  if [[ -f "${SCRIPT_DIR}/${src}" ]]; then
    cp "${SCRIPT_DIR}/${src}" "${dst}"
    log_ok "${label} → ${dst}"
  else
    log_warn "${label} não encontrado em ${SCRIPT_DIR}/${src} — copie manualmente depois"
  fi
}

# Raiz do projeto
copy_if_exists "docker-compose.yml"   "${PROJECT_DIR}/docker-compose.yml"  "docker-compose.yml"
copy_if_exists "bridge.py"            "${PROJECT_DIR}/bridge.py"            "bridge.py"

# Subpastas
copy_if_exists "mosquitto.conf"       "${PROJECT_DIR}/mosquitto/mosquitto.conf"      "mosquitto.conf"
copy_if_exists "nginx.conf"           "${PROJECT_DIR}/nginx/nginx.conf"              "nginx.conf"
copy_if_exists "index.html"           "${PROJECT_DIR}/dashboard/index.html"          "index.html"
copy_if_exists "ml_service.py"        "${PROJECT_DIR}/ml_service/ml_service.py"      "ml_service.py"
copy_if_exists "Dockerfile"           "${PROJECT_DIR}/ml_service/Dockerfile"         "Dockerfile"
copy_if_exists "requirements.txt"     "${PROJECT_DIR}/ml_service/requirements.txt"   "requirements.txt"
copy_if_exists "provision.sh"         "${PROJECT_DIR}/provisioning/provision.sh"     "provision.sh"

# Permissão de execução no provision.sh
if [[ -f "${PROJECT_DIR}/provisioning/provision.sh" ]]; then
  chmod +x "${PROJECT_DIR}/provisioning/provision.sh"
  log_ok "provision.sh marcado como executável"
fi

# =============================================================================
# PASSO 7 — Configuração de firewall (ufw)
# =============================================================================
log_step "PASSO 7: Configuração de firewall (ufw)"

log_info "Configurando regras UFW..."
sudo ufw --force reset > /dev/null 2>&1

# Regras de entrada
sudo ufw default deny incoming  > /dev/null
sudo ufw default allow outgoing > /dev/null
sudo ufw allow 22/tcp           > /dev/null   # SSH — sempre primeiro
sudo ufw allow 8080/tcp         > /dev/null   # Dashboard público

# Porta MQTT — idealmente restrita ao IP da máquina edge
# Aberta para 0.0.0.0 aqui; restrinja no Security Group da AWS ou edite abaixo:
sudo ufw allow 1883/tcp > /dev/null

# Portas internas — NÃO abertas externamente (acesso só via loopback)
# 1026 (Orion), 4041 (IoT Agent), 5001 (ML), 8666 (STH-Comet)
# Já mapeadas para 127.0.0.1 no docker-compose.yml

sudo ufw --force enable > /dev/null

log_ok "UFW ativado com regras:"
sudo ufw status numbered | grep -v "^$" | sed 's/^/      /'

# =============================================================================
# PASSO 8 — Parâmetros do kernel para MongoDB
# =============================================================================
log_step "PASSO 8: Tuning do kernel para MongoDB"

# MongoDB exige vm.max_map_count alto para operações de memória eficientes
if ! grep -q "vm.max_map_count" /etc/sysctl.conf 2>/dev/null; then
  echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf > /dev/null
  sudo sysctl -w vm.max_map_count=262144 > /dev/null
  log_ok "vm.max_map_count=262144 configurado"
else
  log_info "vm.max_map_count já configurado"
fi

# Desabilita Transparent Huge Pages (recomendado pelo MongoDB)
if [[ -f /sys/kernel/mm/transparent_hugepage/enabled ]]; then
  echo never | sudo tee /sys/kernel/mm/transparent_hugepage/enabled > /dev/null
  log_ok "Transparent Huge Pages desabilitado"
fi

# =============================================================================
# PASSO 9 — Verificação final do ambiente
# =============================================================================
log_step "PASSO 9: Verificação final"

ERRORS=0

check() {
  local label="$1" cmd="$2"
  if eval "$cmd" > /dev/null 2>&1; then
    log_ok "${label}"
  else
    log_err "${label} — FALHOU"
    ERRORS=$((ERRORS + 1))
  fi
}

check "Docker daemon ativo"          "sudo systemctl is-active --quiet docker"
check "docker compose disponível"    "docker compose version"
check "Diretório do projeto existe"  "test -d ${PROJECT_DIR}"
check "docker-compose.yml presente"  "test -f ${PROJECT_DIR}/docker-compose.yml"
check "mosquitto.conf presente"      "test -f ${PROJECT_DIR}/mosquitto/mosquitto.conf"
check "nginx.conf presente"          "test -f ${PROJECT_DIR}/nginx/nginx.conf"
check "index.html presente"          "test -f ${PROJECT_DIR}/dashboard/index.html"
check "ml_service.py presente"       "test -f ${PROJECT_DIR}/ml_service/ml_service.py"
check "Dockerfile ML presente"       "test -f ${PROJECT_DIR}/ml_service/Dockerfile"
check "requirements.txt presente"    "test -f ${PROJECT_DIR}/ml_service/requirements.txt"
check "provision.sh executável"      "test -x ${PROJECT_DIR}/provisioning/provision.sh"

if [[ $ERRORS -gt 0 ]]; then
  log_warn "${ERRORS} verificação(ões) falharam. Revise antes de continuar."
else
  log_ok "Todas as verificações passaram."
fi

# =============================================================================
# RESUMO FINAL E PRÓXIMOS PASSOS
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║               INSTALAÇÃO CONCLUÍDA                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

PUBIP=$(curl -sf --max-time 3 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "<IP_DA_EC2>")

echo -e "${BOLD}Próximos passos:${NC}"
echo ""
echo -e "  ${CYAN}1. Aplique o grupo docker SEM reiniciar a sessão:${NC}"
echo -e "     ${YELLOW}newgrp docker${NC}"
echo ""
echo -e "  ${CYAN}2. Entre no diretório do projeto:${NC}"
echo -e "     ${YELLOW}cd ${PROJECT_DIR}${NC}"
echo ""
echo -e "  ${CYAN}3. Suba a stack FIWARE:${NC}"
echo -e "     ${YELLOW}docker compose up -d${NC}"
echo ""
echo -e "  ${CYAN}4. Aguarde todos os containers ficarem healthy (~60s):${NC}"
echo -e "     ${YELLOW}docker compose ps${NC}"
echo ""
echo -e "  ${CYAN}5. Provisione devices e subscriptions (UMA VEZ):${NC}"
echo -e "     ${YELLOW}./provisioning/provision.sh${NC}"
echo ""
echo -e "  ${CYAN}6. Smoke test — publica mensagem de teste:${NC}"
echo -e "     ${YELLOW}docker exec mosquitto mosquitto_pub -h localhost -p 1883 \\"
echo -e "       -t '/plantbridge/prensa-001/attrs' \\"
echo -e "       -m 's=RUNNING|p=10|w=95.0|b=3.0|f=2.0'${NC}"
echo ""
echo -e "  ${CYAN}7. Verifica se o Orion recebeu:${NC}"
echo -e "     ${YELLOW}curl -s http://localhost:1026/v2/entities \\"
echo -e "       -H 'fiware-service: factory' \\"
echo -e "       -H 'fiware-servicepath: /plant' | python3 -m json.tool${NC}"
echo ""
echo -e "  ${CYAN}8. Acesse o dashboard:${NC}"
echo -e "     ${YELLOW}http://${PUBIP}:8080${NC}"
echo ""
echo -e "${BOLD}Lembrete — Security Group da AWS (portas obrigatórias):${NC}"
echo -e "  ${GREEN}22${NC}   → TCP → Seu IP          (SSH)"
echo -e "  ${GREEN}1883${NC} → TCP → IP da máquina edge  (MQTT)"
echo -e "  ${GREEN}8080${NC} → TCP → 0.0.0.0/0        (Dashboard)"
echo ""
echo -e "${BOLD}Projeto em:${NC} ${PROJECT_DIR}"
echo -e "${BOLD}IP público detectado:${NC} ${PUBIP}"
echo ""
