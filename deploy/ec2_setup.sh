#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  AI QA Platform — EC2 Setup Script
#  Target: Ubuntu 22.04/24.04 on t2.micro or t3.micro (free tier)
#
#  What this does:
#    1. Installs Docker + Docker Compose
#    2. Clones your repo from GitHub
#    3. Creates .env from template
#    4. Builds and starts the containers
#    5. Sets up auto-start on reboot
#
#  Usage (on EC2):
#    curl -sSL https://raw.githubusercontent.com/Arun-Engineer/qa-agent/main/deploy/ec2_setup.sh | bash
#    OR
#    scp deploy/ec2_setup.sh ubuntu@<EC2-IP>:~/
#    ssh ubuntu@<EC2-IP> 'bash ec2_setup.sh'
# ═══════════════════════════════════════════════════════════════

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
BOLD='\033[1m'

REPO_URL="https://github.com/Arun-Engineer/qa-agent.git"
APP_DIR="/opt/qa-agent"
BRANCH="main"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   AI QA Platform — EC2 Production Setup                  ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ═══ STEP 1: System updates + Docker ═══
echo -e "${YELLOW}[1/6] Installing Docker...${NC}"

sudo apt-get update -y
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release git

# Docker official repo
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker $USER
    echo -e "  ${GREEN}✓${NC} Docker installed"
else
    echo -e "  ${GREEN}✓${NC} Docker already installed"
fi

# Docker Compose plugin
if ! docker compose version &>/dev/null; then
    sudo apt-get install -y docker-compose-plugin
    echo -e "  ${GREEN}✓${NC} Docker Compose installed"
else
    echo -e "  ${GREEN}✓${NC} Docker Compose already installed"
fi

# Start Docker
sudo systemctl enable docker
sudo systemctl start docker
echo ""

# ═══ STEP 2: Clone repo ═══
echo -e "${YELLOW}[2/6] Cloning repository...${NC}"

if [ -d "$APP_DIR" ]; then
    echo "  Updating existing repo..."
    cd "$APP_DIR"
    sudo git pull origin $BRANCH
else
    sudo git clone -b $BRANCH "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

sudo chown -R $USER:$USER "$APP_DIR"
echo -e "  ${GREEN}✓${NC} Repo at $APP_DIR"
echo ""

# ═══ STEP 3: Create .env ═══
echo -e "${YELLOW}[3/6] Configuring environment...${NC}"

if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"

    # Generate secure keys
    SESSION_SECRET=$(openssl rand -base64 32)
    BOOTSTRAP_KEY=$(openssl rand -base64 48 | tr -d '/+=')

    # Update .env with generated values
    sed -i "s|SESSION_SECRET=.*|SESSION_SECRET=$SESSION_SECRET|" "$APP_DIR/.env"

    # Add production vars if not present
    grep -q "APP_ENV" "$APP_DIR/.env" || echo "APP_ENV=production" >> "$APP_DIR/.env"
    grep -q "ADMIN_BOOTSTRAP_KEY" "$APP_DIR/.env" || echo "ADMIN_BOOTSTRAP_KEY=$BOOTSTRAP_KEY" >> "$APP_DIR/.env"
    grep -q "PLATFORM_GOD_EMAIL" "$APP_DIR/.env" || echo "PLATFORM_GOD_EMAIL=musicstarq@gmail.com" >> "$APP_DIR/.env"

    echo -e "  ${GREEN}✓${NC} .env created"
    echo -e "  ${YELLOW}IMPORTANT: Edit .env to set your API keys:${NC}"
    echo -e "    ${CYAN}sudo nano $APP_DIR/.env${NC}"
    echo ""
    echo -e "  Required:"
    echo "    OPENAI_API_KEY=sk-..."
    echo "    # OR"
    echo "    ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
    echo -e "  Generated bootstrap key: ${BOLD}$BOOTSTRAP_KEY${NC}"
    echo -e "  ${RED}Save this key in a password manager!${NC}"
else
    echo -e "  ${GREEN}✓${NC} .env already exists (not overwriting)"
fi
echo ""

# ═══ STEP 4: Build Docker image ═══
echo -e "${YELLOW}[4/6] Building Docker image...${NC}"

cd "$APP_DIR"
docker compose -f deploy/docker-compose.yml build
echo -e "  ${GREEN}✓${NC} Image built"
echo ""

# ═══ STEP 5: Start containers ═══
echo -e "${YELLOW}[5/6] Starting containers...${NC}"

docker compose -f deploy/docker-compose.yml up -d
echo -e "  ${GREEN}✓${NC} Containers running"
echo ""

# ═══ STEP 6: Auto-start on reboot ═══
echo -e "${YELLOW}[6/6] Setting up auto-start...${NC}"

# Create systemd service
sudo tee /etc/systemd/system/qa-agent.service > /dev/null << 'SYSTEMD'
[Unit]
Description=AI QA Platform
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/qa-agent
ExecStart=/usr/bin/docker compose -f deploy/docker-compose.yml up -d
ExecStop=/usr/bin/docker compose -f deploy/docker-compose.yml down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
SYSTEMD

sudo systemctl daemon-reload
sudo systemctl enable qa-agent.service
echo -e "  ${GREEN}✓${NC} Auto-start enabled"
echo ""

# ═══ VERIFY ═══
echo -e "${YELLOW}[VERIFY]${NC}"
sleep 5

if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} Health check passed"
    HEALTH=$(curl -s http://localhost:8000/health)
    echo -e "  $HEALTH"
else
    echo -e "  ${YELLOW}!${NC} App starting up... check in 30s:"
    echo -e "    ${CYAN}curl http://localhost:8000/health${NC}"
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Deployment complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Get public IP
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "<your-ec2-ip>")

echo -e "  ${BOLD}Access:${NC}"
echo -e "    Local:  http://localhost:8000"
echo -e "    Public: http://$PUBLIC_IP"
echo ""
echo -e "  ${BOLD}Commands:${NC}"
echo -e "    Logs:    ${CYAN}docker compose -f /opt/qa-agent/deploy/docker-compose.yml logs -f${NC}"
echo -e "    Restart: ${CYAN}docker compose -f /opt/qa-agent/deploy/docker-compose.yml restart${NC}"
echo -e "    Stop:    ${CYAN}docker compose -f /opt/qa-agent/deploy/docker-compose.yml down${NC}"
echo -e "    Update:  ${CYAN}cd /opt/qa-agent && git pull && docker compose -f deploy/docker-compose.yml up -d --build${NC}"
echo ""
echo -e "  ${BOLD}SSL setup (optional):${NC}"
echo "    1. Point your domain DNS A record to $PUBLIC_IP"
echo "    2. Edit deploy/nginx.conf — uncomment SSL lines, set your domain"
echo "    3. Run: docker compose -f deploy/docker-compose.yml run certbot certonly --webroot -w /var/www/certbot -d your-domain.com"
echo "    4. Restart: docker compose -f deploy/docker-compose.yml restart nginx"
echo ""
echo -e "  ${BOLD}Edit .env:${NC} ${CYAN}sudo nano /opt/qa-agent/.env${NC}"
echo ""
