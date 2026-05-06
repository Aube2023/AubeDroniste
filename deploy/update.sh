#!/usr/bin/env bash
# =============================================================================
#  AubeDroniste — mise à jour rapide
# =============================================================================
#
#  Usage : sudo bash deploy/update.sh
#
#  Pulle main, MAJ les deps Python si requirements.txt a bougé,
#  redémarre le service, et lance un healthcheck.
# =============================================================================

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/srv/aubedroniste}"
APP_USER="${APP_USER:-aube}"
SERVICE_NAME="aubedroniste"
DOMAIN="${DOMAIN:-droniste.aubeetoilee.com}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()  { printf "${RED}✗${NC} %s\n" "$*" >&2; }

if [[ $EUID -ne 0 ]]; then err "Ce script doit être lancé en root."; exit 1; fi

# Capture le commit avant pull pour comparer
BEFORE=$(sudo -u "$APP_USER" git -C "$INSTALL_DIR" rev-parse HEAD)
sudo -u "$APP_USER" git -C "$INSTALL_DIR" fetch --quiet
sudo -u "$APP_USER" git -C "$INSTALL_DIR" pull --quiet --ff-only
AFTER=$(sudo -u "$APP_USER" git -C "$INSTALL_DIR" rev-parse HEAD)

if [[ "$BEFORE" == "$AFTER" ]]; then
    ok "Déjà à jour ($(echo $AFTER | cut -c1-7))."
    exit 0
fi

ok "Pull OK : $BEFORE → $AFTER"

# Si requirements.txt a bougé, on réinstalle
if sudo -u "$APP_USER" git -C "$INSTALL_DIR" diff --name-only "$BEFORE" "$AFTER" | grep -q "^requirements.txt$"; then
    ok "requirements.txt a changé — réinstallation des dépendances"
    sudo -u "$APP_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade -r "$INSTALL_DIR/requirements.txt"
fi

# Si schema.sql a bougé, on log un avertissement (les migrations sont manuelles)
if sudo -u "$APP_USER" git -C "$INSTALL_DIR" diff --name-only "$BEFORE" "$AFTER" | grep -q "^schema.sql$"; then
    warn "schema.sql a changé. Vérifie qu'aucune migration manuelle n'est requise."
fi

systemctl restart "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service $SERVICE_NAME redémarré."
else
    err "Service $SERVICE_NAME inactif après restart. Logs :"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager
    exit 1
fi

# Healthcheck rapide
CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/api/stats" || echo "000")
if [[ "$CODE" == "200" ]]; then
    ok "https://$DOMAIN/api/stats répond 200"
else
    warn "Externe répond $CODE"
fi

ok "Mise à jour terminée."
