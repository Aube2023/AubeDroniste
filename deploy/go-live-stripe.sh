#!/usr/bin/env bash
# =============================================================================
#  AubePilot — passage de Stripe en mode LIVE (production)
# =============================================================================
#
#  Usage (sur le SERVEUR, en root) :
#     sudo bash /srv/aubepilot/deploy/go-live-stripe.sh
#
#  Ce script demande les 3 valeurs Stripe LIVE en interactif, les écrit dans
#  l'env file de prod (jamais affichées, jamais loggées), fait une sauvegarde
#  de l'ancien fichier, puis redémarre le service et lance le healthcheck.
#
#  Les clés ne transitent QUE par ce serveur. Rien n'est envoyé ailleurs.
# =============================================================================

set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/aubepilot.env}"
SERVICE_NAME="aubepilot"
INSTALL_DIR="${INSTALL_DIR:-/srv/aubepilot}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()  { printf "${RED}✗${NC} %s\n" "$*" >&2; }

if [[ $EUID -ne 0 ]]; then err "À lancer en root (sudo)."; exit 1; fi
if [[ ! -f "$ENV_FILE" ]]; then err "Env file introuvable : $ENV_FILE"; exit 1; fi

echo
echo "  Passage de Stripe en mode LIVE — pilot.aubeetoilee.com"
echo "  ----------------------------------------------------"
echo "  Récupère les 3 valeurs dans dashboard.stripe.com (bascule sur 'Live') :"
echo "   • Developers → API keys      → clé secrète   sk_live_..."
echo "   • Developers → API keys      → clé publique  pk_live_..."
echo "   • Developers → Webhooks      → signing secret whsec_..."
echo

# -- Saisie (la clé secrète est masquée : -s) --------------------------------
read -r -s -p "  STRIPE_SECRET_KEY (sk_live_...)     : " SK; echo
read -r    -p "  STRIPE_PUBLISHABLE_KEY (pk_live_...) : " PK
read -r -s -p "  STRIPE_WEBHOOK_SECRET (whsec_...)    : " WH; echo
echo

# -- Validations de base -----------------------------------------------------
if [[ "$SK" != sk_live_* ]]; then
    err "La clé secrète ne commence pas par 'sk_live_'. Es-tu bien en mode Live ? Abandon."
    exit 1
fi
if [[ "$PK" != pk_live_* ]]; then
    err "La clé publique ne commence pas par 'pk_live_'. Abandon."
    exit 1
fi
if [[ "$WH" != whsec_* ]]; then
    err "Le webhook secret ne commence pas par 'whsec_'. Abandon."
    exit 1
fi

# -- Sauvegarde de l'env file actuel -----------------------------------------
BACKUP="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
cp -a "$ENV_FILE" "$BACKUP"
chmod 600 "$BACKUP"
ok "Sauvegarde de l'ancien env file : $BACKUP"

# -- Remplace (ou ajoute) chaque variable sans toucher au reste ---------------
#    Pas de sed : le secret ne doit jamais apparaître en argument d'une
#    commande externe (visible dans `ps`), et les métacaractères sed (& \)
#    le corrompraient. printf est un builtin : rien ne fuite.
set_var() {
    local key="$1" val="$2" tmp
    tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
    chmod 600 "$tmp"
    grep -vE "^${key}=" "$ENV_FILE" > "$tmp" || true
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
    mv "$tmp" "$ENV_FILE"
}
set_var "STRIPE_SECRET_KEY"      "$SK"
set_var "STRIPE_PUBLISHABLE_KEY" "$PK"
set_var "STRIPE_WEBHOOK_SECRET"  "$WH"
chmod 600 "$ENV_FILE"
ok "Clés Stripe LIVE écrites dans $ENV_FILE (permissions 600)."

# -- Redémarrage du service --------------------------------------------------
systemctl restart "$SERVICE_NAME"
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service $SERVICE_NAME redémarré."
else
    err "Le service n'a pas redémarré. Restaure : cp -a $BACKUP $ENV_FILE && systemctl restart $SERVICE_NAME"
    exit 1
fi

# -- Healthcheck si dispo ----------------------------------------------------
if [[ -x "$INSTALL_DIR/deploy/healthcheck.sh" ]]; then
    bash "$INSTALL_DIR/deploy/healthcheck.sh" || warn "Healthcheck a relevé des points à vérifier."
fi

echo
ok "Stripe est maintenant en mode LIVE."
echo "  Vérifie sur le site que le bandeau affiche 'LIVE' (plus 'TEST')."
echo "  Rappels :"
echo "   • chaque pilote doit refaire son onboarding Connect en live ;"
echo "   • un paiement test débitera une vraie carte."
echo
