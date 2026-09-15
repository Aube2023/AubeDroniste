#!/usr/bin/env bash
# =============================================================================
#  AubePilot — brancher (ou rebrancher) Stripe en mode LIVE sur le serveur
# =============================================================================
#
#  Usage (sur le SERVEUR, en root) :
#     sudo bash /srv/aubepilot/deploy/go-live-stripe.sh
#     sudo EXPECTED_ACCOUNT=acct_xxx bash /srv/aubepilot/deploy/go-live-stripe.sh
#
#  Le script demande la clé secrète et la clé publiable LIVE du compte
#  plateforme, puis fait tout le reste lui-même, à l'API :
#   1. vérifie que la clé répond, affiche le compte (id, nom, pays, devise,
#      encaissement, virements) et refuse un autre compte que EXPECTED_ACCOUNT ;
#   2. vérifie que Connect est ouvert sur ce compte (liste des comptes
#      connectés lisible) ;
#   3. supprime les webhooks existants vers nos URL et en crée deux neufs :
#      plateforme (paiements, remboursements) et Connect (comptes pilotes :
#      account.updated) ; les secrets sont récupérés à la création, jamais
#      affichés ;
#   4. écrit les variables dans l'env file (sauvegarde avant), dont
#      STRIPE_CONNECT_ENABLED=1, STRIPE_ACCOUNT_ID et STRIPE_PLATFORM_COUNTRY ;
#   5. redémarre le service et lance le healthcheck.
#
#  Les clés ne transitent QUE par ce serveur. Rien n'est envoyé ailleurs.
#  Aucune clé ne passe en argument d'une commande (curl lit sa config sur
#  stdin, les variables sont écrites par printf, un builtin).
# =============================================================================

set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/aubepilot.env}"
SERVICE_NAME="aubepilot"
INSTALL_DIR="${INSTALL_DIR:-/srv/aubepilot}"
EXPECTED_ACCOUNT="${EXPECTED_ACCOUNT:-}"
API="https://api.stripe.com/v1"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()  { printf "${RED}✗${NC} %s\n" "$*" >&2; }

if [[ $EUID -ne 0 ]]; then err "À lancer en root (sudo)."; exit 1; fi
if [[ ! -f "$ENV_FILE" ]]; then err "Env file introuvable : $ENV_FILE"; exit 1; fi
command -v curl >/dev/null || { err "curl manquant."; exit 1; }
command -v python3 >/dev/null || { err "python3 manquant."; exit 1; }

# Adresse publique du site, d'après l'env file (SITE_URL) ; repli sur le domaine de prod.
SITE_URL="$(grep -E '^SITE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' || true)"
SITE_URL="${SITE_URL:-https://pilot.aubeetoilee.com}"
WEBHOOK_URL="${SITE_URL%/}/stripe/webhook"
CONNECT_WEBHOOK_URL="${SITE_URL%/}/stripe/webhook/connect"

echo
echo "  Stripe en mode LIVE — ${SITE_URL#https://}"
echo "  ----------------------------------------------------"
echo "  Dans dashboard.stripe.com, sur le compte qui a Connect, en mode réel :"
echo "   • Développeurs → Clés API → clé secrète   sk_live_..."
echo "   • Développeurs → Clés API → clé publiable pk_live_..."
echo "  Le webhook et son secret sont créés ici, pas besoin de les copier."
echo

# -- Saisie (la clé secrète est masquée : -s) --------------------------------
read -r -s -p "  STRIPE_SECRET_KEY (sk_live_...)      : " SK; echo
read -r    -p "  STRIPE_PUBLISHABLE_KEY (pk_live_...) : " PK
echo

if [[ "$SK" != sk_live_* ]]; then
    err "La clé secrète ne commence pas par 'sk_live_'. Es-tu bien en mode réel ? Abandon."
    exit 1
fi
if [[ "$PK" != pk_live_* ]]; then
    err "La clé publiable ne commence pas par 'pk_live_'. Abandon."
    exit 1
fi

# -- Appels API : la clé passe par la config stdin de curl, jamais en argument.
api() {
    # api GET|POST|DELETE chemin [param=valeur ...]
    local method="$1" path="$2"; shift 2
    local args=()
    for kv in "$@"; do args+=(--data-urlencode "$kv"); done
    printf 'user = "%s:"\n' "$SK" | curl -sS -K - -X "$method" "${API}${path}" ${args[@]+"${args[@]}"}
}
# jget <clé[.clé...]> : lit un champ JSON sur stdin (vide si absent ou erreur API).
jget() {
    python3 -c '
import json, sys
d = json.load(sys.stdin)
if isinstance(d, dict) and "error" in d:
    print("__ERROR__ " + str(d["error"].get("message", d["error"])))
    sys.exit(0)
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else d)' "$1"
}

# -- 1. Le compte qui répond -------------------------------------------------
ACC_JSON="$(api GET /account)"
ACC_ID="$(printf '%s' "$ACC_JSON" | jget id)"
if [[ "$ACC_ID" == __ERROR__* ]]; then err "Stripe refuse la clé : ${ACC_ID#__ERROR__ }"; exit 1; fi
if [[ -z "$ACC_ID" ]]; then err "Réponse inattendue de Stripe (pas d'identifiant de compte). Abandon."; exit 1; fi
ACC_NAME="$(printf '%s' "$ACC_JSON" | jget settings.dashboard.display_name)"
[[ -n "$ACC_NAME" ]] || ACC_NAME="$(printf '%s' "$ACC_JSON" | jget business_profile.name)"
ACC_COUNTRY="$(printf '%s' "$ACC_JSON" | jget country)"
ACC_CURRENCY="$(printf '%s' "$ACC_JSON" | jget default_currency)"
ACC_CHARGES="$(printf '%s' "$ACC_JSON" | jget charges_enabled)"
ACC_PAYOUTS="$(printf '%s' "$ACC_JSON" | jget payouts_enabled)"
echo "  Compte : $ACC_ID  « ${ACC_NAME:-?} »  ${ACC_COUNTRY:-?} · ${ACC_CURRENCY^^}"
echo "           encaissement=${ACC_CHARGES}  virements=${ACC_PAYOUTS}"
if [[ -n "$EXPECTED_ACCOUNT" && "$ACC_ID" != "$EXPECTED_ACCOUNT" ]]; then
    err "Cette clé appartient à $ACC_ID, pas à $EXPECTED_ACCOUNT. Mauvais compte Stripe. Abandon."
    exit 1
fi
if [[ "$ACC_CHARGES" != "True" || "$ACC_PAYOUTS" != "True" ]]; then
    err "Ce compte n'a pas encaissement + virements actifs. Finir son dossier dans le dashboard. Abandon."
    exit 1
fi
if [[ -z "$EXPECTED_ACCOUNT" ]]; then
    read -r -p "  Brancher AubePilot sur ce compte ? (oui/non) : " CONFIRM
    [[ "$CONFIRM" == "oui" ]] || { warn "Abandon, rien n'a été modifié."; exit 0; }
fi
ok "Clé secrète valide pour $ACC_ID."

# -- 2. Connect ouvert ? -----------------------------------------------------
LIST="$(api GET '/accounts?limit=1' | jget object)"
if [[ "$LIST" == __ERROR__* ]]; then
    err "Connect n'est pas ouvert sur ce compte : ${LIST#__ERROR__ }"
    err "Dashboard → Connect → Commencer (comptes Express), puis relancer ce script."
    exit 1
fi
ok "Connect répond (liste des comptes connectés lisible)."

# -- 3. Webhooks : supprimer les nôtres, en créer deux neufs -----------------
OLD_IDS="$(api GET '/webhook_endpoints?limit=100' | python3 -c '
import json, sys
d = json.load(sys.stdin)
for w in d.get("data", []):
    if w.get("url") in sys.argv[1:]:
        print(w["id"])' "$WEBHOOK_URL" "$CONNECT_WEBHOOK_URL")"
for wid in $OLD_IDS; do
    api DELETE "/webhook_endpoints/$wid" >/dev/null && ok "Ancien webhook $wid supprimé."
done
# Plateforme : paiements et remboursements (évènements du compte lui-même).
WH_JSON="$(api POST /webhook_endpoints "url=$WEBHOOK_URL" \
    "enabled_events[]=checkout.session.completed" \
    "enabled_events[]=charge.refunded" \
    "enabled_events[]=charge.dispute.created" \
    "description=AubePilot paiements (créé par deploy/go-live-stripe.sh)")"
WH_ID="$(printf '%s' "$WH_JSON" | jget id)"
if [[ "$WH_ID" == __ERROR__* || -z "$WH_ID" ]]; then err "Création du webhook refusée : ${WH_ID#__ERROR__ }"; exit 1; fi
WH="$(printf '%s' "$WH_JSON" | jget secret)"
if [[ "$WH" != whsec_* ]]; then err "Stripe n'a pas renvoyé le secret du webhook $WH_ID. Abandon."; exit 1; fi
ok "Webhook $WH_ID créé vers $WEBHOOK_URL (paiements), secret récupéré."
# Connect : évènements des comptes pilotes (activation des virements). Stripe
# ne les livre qu'à un endpoint créé avec connect=true, secret distinct.
CWH_JSON="$(api POST /webhook_endpoints "url=$CONNECT_WEBHOOK_URL" "connect=true" \
    "enabled_events[]=account.updated" \
    "description=AubePilot comptes pilotes (créé par deploy/go-live-stripe.sh)")"
CWH_ID="$(printf '%s' "$CWH_JSON" | jget id)"
if [[ "$CWH_ID" == __ERROR__* || -z "$CWH_ID" ]]; then err "Création du webhook Connect refusée : ${CWH_ID#__ERROR__ }"; exit 1; fi
CWH="$(printf '%s' "$CWH_JSON" | jget secret)"
if [[ "$CWH" != whsec_* ]]; then err "Stripe n'a pas renvoyé le secret du webhook Connect $CWH_ID. Abandon."; exit 1; fi
ok "Webhook Connect $CWH_ID créé vers $CONNECT_WEBHOOK_URL (account.updated), secret récupéré."

# -- 4. Env file -------------------------------------------------------------
BACKUP="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
cp -a "$ENV_FILE" "$BACKUP"
chmod 600 "$BACKUP"
ok "Sauvegarde de l'ancien env file : $BACKUP"

# Remplace (ou ajoute) chaque variable sans toucher au reste. Pas de sed : le
# secret ne doit jamais apparaître en argument d'une commande externe (visible
# dans `ps`), et les métacaractères sed (& \) le corrompraient.
OWNER="$(stat -c '%U:%G' "$ENV_FILE")"
MODE="$(stat -c '%a' "$ENV_FILE")"
set_var() {
    local key="$1" val="$2" tmp
    tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
    grep -vE "^${key}=" "$ENV_FILE" > "$tmp" || true
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
    chown "$OWNER" "$tmp"; chmod "$MODE" "$tmp"
    mv "$tmp" "$ENV_FILE"
}
set_var "STRIPE_SECRET_KEY"      "$SK"
set_var "STRIPE_PUBLISHABLE_KEY" "$PK"
set_var "STRIPE_WEBHOOK_SECRET"  "$WH"
set_var "STRIPE_CONNECT_WEBHOOK_SECRET" "$CWH"
set_var "STRIPE_ACCOUNT_ID"      "$ACC_ID"
set_var "STRIPE_PLATFORM_COUNTRY" "${ACC_COUNTRY^^}"
set_var "AUBEPILOT_ALLOW_FAKE_PAYMENTS" "0"
set_var "STRIPE_CONNECT_ENABLED" "1"
ok "Variables Stripe écrites dans $ENV_FILE ($OWNER, $MODE)."

# -- 5. Redémarrage du service -----------------------------------------------
systemctl restart "$SERVICE_NAME"
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service $SERVICE_NAME redémarré."
else
    err "Le service n'a pas redémarré. Restaure : cp -a $BACKUP $ENV_FILE && systemctl restart $SERVICE_NAME"
    exit 1
fi

if [[ -x "$INSTALL_DIR/deploy/healthcheck.sh" || -f "$INSTALL_DIR/deploy/healthcheck.sh" ]]; then
    bash "$INSTALL_DIR/deploy/healthcheck.sh" || warn "Healthcheck a relevé des points à vérifier."
fi

echo
ok "Stripe est branché en mode LIVE sur $ACC_ID (Connect actif)."
echo "  Vérifier ${SITE_URL}/admin/stripe : compte « attendu », deux webhooks « le nôtre », rien de manquant."
echo "  Rappels :"
echo "   • chaque pilote fait son onboarding Connect depuis son espace ;"
echo "   • un paiement test débitera une vraie carte ;"
echo "   • révoquer l'ancienne clé secrète dans le dashboard de l'ancien compte si elle a circulé."
echo
