#!/usr/bin/env bash
# Phase 0: Housekeeping — run once from repo root
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[OK]${NC}   $1"; }
log_warn()  { echo -e "${YELLOW}[FIX]${NC}  $1"; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
echo "=== Phase 0: Housekeeping — $REPO ==="

# 1. Remove .env from git (SECURITY)
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
    git rm --cached .env
    log_warn ".env REMOVED from git index — ROTATE ALL SECRETS NOW"
else log_info ".env not tracked"; fi

# 2. Remove __pycache__
PYCACHE=$(git ls-files '*__pycache__*' 2>/dev/null || true)
if [ -n "$PYCACHE" ]; then
    echo "$PYCACHE" | xargs -r git rm --cached 2>/dev/null || true
    log_warn "__pycache__ removed from git"
else log_info "No __pycache__ tracked"; fi

# 3. Remove .idea/
IDEA=$(git ls-files '.idea/*' 2>/dev/null || true)
if [ -n "$IDEA" ]; then
    echo "$IDEA" | xargs -r git rm --cached
    log_warn ".idea/ removed from git"
else log_info ".idea/ not tracked"; fi

# 4. Ensure .gitignore has required entries
PATTERNS=("__pycache__/" "*.py[cod]" ".env" ".idea/" ".vscode/" "logs/" "reports/*" ".pytest_cache/" ".coverage" "*.egg-info/" "data/chroma/" "data/app.db")
MISSING=()
for p in "${PATTERNS[@]}"; do
    grep -qF "$p" .gitignore 2>/dev/null || MISSING+=("$p")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "" >> .gitignore
    echo "# === Phase 0 Housekeeping ===" >> .gitignore
    for p in "${MISSING[@]}"; do echo "$p" >> .gitignore; log_warn "Added to .gitignore: $p"; done
else log_info ".gitignore complete"; fi

# 5. Ensure key directories exist
for d in src src/api src/api/routes src/api/middleware src/session src/models src/guardrails src/discovery src/ingestion config tests deploy; do
    [ -d "$d" ] && log_info "  ✓ $d/" || { mkdir -p "$d"; log_warn "  + Created $d/"; }
done

# 6. Flag stale root files that should be consolidated
for f in api.py app.py main_api.py api_server.py; do
    [ -f "$f" ] && log_warn "STALE: $f — now consolidated into src/api/main.py (keep for backward compat or delete)"
done

echo ""
echo "=== Phase 0 Complete ==="
echo "Next: git diff --cached && git commit -m 'chore: Phase 0 housekeeping'"
echo "IMPORTANT: Rotate any secrets that were in .env"
