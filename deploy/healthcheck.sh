#!/usr/bin/env bash
# =============================================================================
#  AubePilot — healthcheck post-déploiement
# =============================================================================
#
#  Usage : bash deploy/healthcheck.sh
#
#  Vérifie 12 points clés de la prod en 30 secondes.
#  Code de sortie : 0 si tout OK, 1 si au moins un fail.
# =============================================================================

set -uo pipefail

DOMAIN="${DOMAIN:-pilot.aubeetoilee.com}"
INSTALL_DIR="${INSTALL_DIR:-/srv/aubepilot}"
DATA_DIR="${DATA_DIR:-/var/lib/aubepilot}"
ENV_FILE="${ENV_FILE:-/etc/aubepilot.env}"
SERVICE_NAME="aubepilot"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
PASS=0; FAIL=0; WARN=0
check_ok()    { printf "  ${GREEN}✓${NC} %s\n" "$*"; PASS=$((PASS+1)); }
check_fail()  { printf "  ${RED}✗${NC} %s\n" "$*"; FAIL=$((FAIL+1)); }
check_warn()  { printf "  ${YELLOW}⚠${NC} %s\n" "$*"; WARN=$((WARN+1)); }

printf "${CYAN}AubePilot — healthcheck $DOMAIN${NC}\n\n"

# 1. Service actif
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    check_ok "systemd : $SERVICE_NAME actif"
else
    check_fail "systemd : $SERVICE_NAME inactif"
fi

# 2. App répond en local
CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:5034/api/stats" 2>/dev/null || echo "000")
if [[ "$CODE" == "200" ]]; then
    check_ok "App locale : 127.0.0.1:5034 → 200"
else
    check_fail "App locale : 127.0.0.1:5034 → $CODE"
fi

# 3. nginx actif
if systemctl is-active --quiet nginx 2>/dev/null; then
    check_ok "nginx : actif"
else
    check_fail "nginx : inactif"
fi

# 4. DNS
if command -v dig &>/dev/null; then
    IP=$(dig +short "$DOMAIN" 2>/dev/null | head -1)
    if [[ -n "$IP" ]]; then
        check_ok "DNS : $DOMAIN → $IP"
    else
        check_fail "DNS : $DOMAIN ne résout pas"
    fi
fi

# 5. HTTPS répond
EXTERNAL=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/api/stats" --max-time 10 2>/dev/null || echo "000")
if [[ "$EXTERNAL" == "200" ]]; then
    check_ok "Public : https://$DOMAIN/api/stats → 200"
else
    check_fail "Public : https://$DOMAIN → $EXTERNAL"
fi

# 6. Cert valide
if command -v openssl &>/dev/null; then
    EXPIRY=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
              | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    if [[ -n "$EXPIRY" ]]; then
        check_ok "Cert SSL : valide jusqu'au $EXPIRY"
    else
        check_warn "Cert SSL : impossible de lire la date d'expiration"
    fi
fi

# 7. Headers de sécurité
HEADERS=$(curl -sI "https://$DOMAIN/" --max-time 10 2>/dev/null || echo "")
for h in "X-Frame-Options" "X-Content-Type-Options" "Strict-Transport-Security" "Content-Security-Policy" "Referrer-Policy"; do
    if echo "$HEADERS" | grep -qi "^$h:"; then
        check_ok "Header : $h présent"
    else
        check_fail "Header : $h absent"
    fi
done

# 8. CSRF refus sur POST sans token
CSRF_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "https://$DOMAIN/inscription" \
            -d "username=hack&password=x" --max-time 10 2>/dev/null || echo "000")
if [[ "$CSRF_CODE" == "403" ]]; then
    check_ok "CSRF : POST sans token → 403 (rejeté)"
else
    check_fail "CSRF : POST sans token → $CSRF_CODE (attendu 403)"
fi

# 9. Env file durci
if [[ -f "$ENV_FILE" ]]; then
    PERMS=$(stat -c "%a" "$ENV_FILE" 2>/dev/null || stat -f "%A" "$ENV_FILE" 2>/dev/null)
    if [[ "$PERMS" == "600" || "$PERMS" == "640" ]]; then
        check_ok "Env file : $ENV_FILE chmod $PERMS"
    else
        check_fail "Env file : $ENV_FILE chmod $PERMS (attendu 600 ou 640)"
    fi
    if grep -q "^AUBEPILOT_SECRET=$" "$ENV_FILE" 2>/dev/null; then
        check_fail "Env file : AUBEPILOT_SECRET vide"
    elif grep -q "AUBEPILOT_SECRET=change-me" "$ENV_FILE" 2>/dev/null; then
        check_fail "Env file : AUBEPILOT_SECRET = valeur par défaut !"
    else
        check_ok "Env file : AUBEPILOT_SECRET défini"
    fi
    if grep -q "^STRIPE_SECRET_KEY=$" "$ENV_FILE" 2>/dev/null; then
        check_warn "Stripe : pas de clé (mode FAKE actif)"
    elif grep -q "^STRIPE_SECRET_KEY=sk_live_" "$ENV_FILE" 2>/dev/null; then
        check_ok "Stripe : LIVE configuré"
    elif grep -q "^STRIPE_SECRET_KEY=sk_test_" "$ENV_FILE" 2>/dev/null; then
        check_warn "Stripe : TEST configuré (pas LIVE)"
    fi
fi

# 10. .dev_passwords absent (jamais sur prod)
if [[ -f "$INSTALL_DIR/.dev_passwords" ]]; then
    check_fail ".dev_passwords présent sur le serveur — supprimer immédiatement"
else
    check_ok ".dev_passwords absent (OK)"
fi

# 11. Cron en place
if [[ -f /etc/cron.d/aubepilot ]]; then
    check_ok "Cron : /etc/cron.d/aubepilot présent"
else
    check_warn "Cron : auto-release / backup absents"
fi

# 12. DB lisible et tests
if [[ -f "$DATA_DIR/aubepilot.db" ]]; then
    NB=$(sqlite3 "$DATA_DIR/aubepilot.db" "SELECT COUNT(*) FROM users" 2>/dev/null || echo "?")
    check_ok "DB : $DATA_DIR/aubepilot.db ($NB users)"
else
    check_fail "DB : $DATA_DIR/aubepilot.db manquant"
fi

# Bilan
printf "\n${CYAN}Bilan :${NC} ${GREEN}%d OK${NC}, ${YELLOW}%d warn${NC}, ${RED}%d fail${NC}\n\n" "$PASS" "$WARN" "$FAIL"
[[ $FAIL -gt 0 ]] && exit 1
exit 0
