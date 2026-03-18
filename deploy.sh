#!/usr/bin/env bash
# deploy.sh — Commit all qa_agent changes to GitHub and deploy to AWS EC2
# Run from qa_agent root: bash deploy.sh

set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"

echo ""
echo "================================================"
echo " QA Agent — Git Commit + AWS EC2 Deploy"
echo "================================================"
echo ""

# ── CONFIG — edit these ───────────────────────────
EC2_HOST="${EC2_HOST:-}"           # e.g. ec2-13-233-xx-xx.ap-south-1.compute.amazonaws.com
EC2_USER="${EC2_USER:-ubuntu}"     # usually ubuntu or ec2-user
EC2_KEY="${EC2_KEY:-}"             # path to .pem file e.g. ~/Downloads/qa_agent.pem
EC2_REPO="${EC2_REPO:-/home/ubuntu/qa_agent}"  # path on EC2
# ─────────────────────────────────────────────────

# ── Step 1: Show changed files ────────────────────
echo "[1] Changed files:"
git status --short
echo ""

# ── Step 2: Stage all changes ─────────────────────
echo "[2] Staging all changes..."
git add -A
echo "    Done"

# ── Step 3: Commit ────────────────────────────────
TIMESTAMP=$(date +"%Y-%m-%d %H:%M")
MSG="${1:-"fix: auth flow, delivery address, Excel reporting, credential handling [$TIMESTAMP]"}"
echo "[3] Committing: $MSG"
git commit -m "$MSG" || echo "    Nothing new to commit"

# ── Step 4: Push to GitHub ────────────────────────
echo ""
echo "[4] Pushing to GitHub..."
git push origin main
echo "    Pushed!"

# ── Step 5: Deploy to EC2 ─────────────────────────
if [ -z "$EC2_HOST" ] || [ -z "$EC2_KEY" ]; then
    echo ""
    echo "⚠️  EC2_HOST or EC2_KEY not set — skipping EC2 deploy"
    echo "   To deploy to EC2, run:"
    echo "   EC2_HOST=your-ec2-host EC2_KEY=~/your-key.pem bash deploy.sh"
    echo ""
    echo "================================================"
    echo " Done! Code pushed to GitHub."
    echo "================================================"
    exit 0
fi

echo ""
echo "[5] Deploying to EC2: $EC2_USER@$EC2_HOST"
echo "    Repo path: $EC2_REPO"

ssh -i "$EC2_KEY" -o StrictHostKeyChecking=no "$EC2_USER@$EC2_HOST" << REMOTE
set -e
echo "--- Pulling latest code ---"
cd "$EC2_REPO"
git pull origin main

echo "--- Installing dependencies ---"
pip install -r requirements.txt --quiet 2>/dev/null || true

echo "--- Restarting server ---"
# Try systemd first
if systemctl is-active --quiet qa-agent 2>/dev/null; then
    sudo systemctl restart qa-agent
    echo "Restarted via systemctl"
elif command -v pm2 &>/dev/null && pm2 list | grep -q qa-agent; then
    pm2 restart qa-agent
    echo "Restarted via pm2"
else
    # Kill existing uvicorn and restart
    pkill -f "uvicorn asgi:app" 2>/dev/null || true
    sleep 2
    nohup python -m uvicorn asgi:app \
        --host 0.0.0.0 \
        --port 8000 \
        --workers 1 \
        > logs/uvicorn.log 2>&1 &
    echo "Restarted via nohup, PID: $!"
fi

echo "--- Server status ---"
sleep 2
curl -s http://localhost:8000/api/metrics | python3 -c "import sys,json; d=json.load(sys.stdin); print('Server OK:', d)" 2>/dev/null || echo "Server starting up..."
REMOTE

echo ""
echo "================================================"
echo " Deployment complete!"
echo "================================================"
echo ""
echo " GitHub: pushed to main"
echo " EC2: $EC2_HOST"
echo " App: http://$EC2_HOST:8000/agent-ui"
echo ""
