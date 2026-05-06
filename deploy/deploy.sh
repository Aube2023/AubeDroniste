#!/usr/bin/env bash
# =============================================================================
#  AubeDroniste — installation / mise à jour automatisée pour serveur Linux
# =============================================================================
#
#  Usage :
#    sudo bash deploy/deploy.sh                 # install ou MAJ complète
#    sudo bash deploy/deploy.sh --skip-cert     # n'appelle pas certbot
#    sudo bash deploy/deploy.sh --dry-run       # affiche sans exécuter
#
#  Variables d'env honorées (sinon valeurs par défaut) :
#    DOMAIN          droniste.aubeetoilee.com
#    APP_USER        aube
#    INSTALL_DIR     /srv/aubedroniste
#    DATA_DIR        /var/lib/aubedroniste
#    ENV_FILE        /etc/aubedroniste.env
#    REPO_URL        https://github.com/Aube2023/AubeDroniste.git
#    ADMIN_EMAIL     no-reply@aubeetoilee.com   (pour Let's Encrypt)
#    BRANCH          main
#
#  Étapes idempotentes :
#    1. Prérequis  (debian/ubuntu, root, dépendances système)
#    2. User       (création de `aube` si absent)
#    3. Répertoires (install, data, log, backup) + permissions
#    4. Code       (git clone ou pull)
#    5. Python     (venv + pip install -r requirements.txt + gunicorn)
#    6. Env file   (génère AUBEDRONISTE_SECRET, écrit /etc/aubedroniste.env)
#    7. Database   (init schema.sql si absent)
#    8. systemd    (copie unit, enable, start)
#    9. nginx      (copie conf, enable, reload)
#   10. SSL        (certbot --nginx)
#   11. Cron       (auto-release J+7 + backup quotidien)
#   12. Healthcheck (curl, statut systemd)
# =============================================================================

set -euo pipefail

# ----- Config (valeurs par défaut, surchargées par les env vars) -------------
DOMAIN="${DOMAIN:-droniste.aubeetoilee.com}"
APP_USER="${APP_USER:-aube}"
INSTALL_DIR="${INSTALL_DIR:-/srv/aubedroniste}"
DATA_DIR="${DATA_DIR:-/var/lib/aubedroniste}"
LOG_DIR="${LOG_DIR:-/var/log/aubedroniste}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/aubedroniste}"
ENV_FILE="${ENV_FILE:-/etc/aubedroniste.env}"
REPO_URL="${REPO_URL:-https://github.com/Aube2023/AubeDroniste.git}"
ADMIN_EMAIL="${ADMIN_EMAIL:-no-reply@aubeetoilee.com}"
BRANCH="${BRANCH:-main}"
PORT="${PORT:-5034}"
SERVICE_NAME="aubedroniste"

DRY_RUN=0
SKIP_CERT=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY_RUN=1 ;;
        --skip-cert) SKIP_CERT=1 ;;
        --help|-h)
            sed -n '1,30p' "$0"; exit 0 ;;
        *)
            echo "Argument inconnu : $arg" >&2; exit 2 ;;
    esac
done

# ----- Helpers d'affichage ---------------------------------------------------
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
STEP=0
log()    { printf "${CYAN}[%2d]${NC} %s\n" "$STEP" "$*"; }
ok()     { printf "    ${GREEN}✓${NC} %s\n" "$*"; }
warn()   { printf "    ${YELLOW}⚠${NC} %s\n" "$*"; }
err()    { printf "    ${RED}✗${NC} %s\n" "$*" >&2; }
step()   { STEP=$((STEP+1)); printf "\n${CYAN}[%2d] %s${NC}\n" "$STEP" "$*"; }

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "    DRY: $*"
    else
        eval "$@"
    fi
}

trap 'err "Échec à l étape $STEP. Voir messages ci-dessus."; exit 1' ERR

# =============================================================================
#  1. Prérequis
# =============================================================================

step "Vérification des prérequis"

if [[ $EUID -ne 0 ]]; then
    err "Ce script doit être lancé en root (sudo)."; exit 1
fi
if ! grep -qE 'ubuntu|debian' /etc/os-release; then
    err "Distribution non supportée (Debian/Ubuntu attendu)."; exit 1
fi
ok "OS : $(lsb_release -ds 2>/dev/null || cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2)"

# Liste des packages à installer
PACKAGES=(
    python3 python3-venv python3-pip
    git curl jq sqlite3
    nginx certbot python3-certbot-nginx
    libpam0g-dev   # pour python-pam
)
NEEDED=()
for pkg in "${PACKAGES[@]}"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        NEEDED+=("$pkg")
    fi
done
if [[ ${#NEEDED[@]} -gt 0 ]]; then
    log "Installation : ${NEEDED[*]}"
    run "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ${NEEDED[*]}"
fi
ok "Tous les paquets nécessaires sont installés."

# =============================================================================
#  2. User aube
# =============================================================================

step "User système \`$APP_USER\`"

if ! id "$APP_USER" >/dev/null 2>&1; then
    log "Création du user $APP_USER (sans shell login, home /var/lib/$APP_USER)"
    run "useradd --system --create-home --home-dir /var/lib/$APP_USER --shell /usr/sbin/nologin $APP_USER"
fi
# Ajout au groupe `shadow` : indispensable pour que pam_unix.so puisse
# lire /etc/shadow et valider les mots de passe AubeMail (PAM service `aube`).
if ! id -nG "$APP_USER" | grep -qw shadow; then
    log "Ajout de $APP_USER au groupe shadow (lecture /etc/shadow pour PAM)"
    run "usermod -aG shadow $APP_USER"
fi
ok "User $APP_USER existe (groupes: $(id -nG $APP_USER 2>/dev/null || echo '?'))."

# =============================================================================
#  3. Répertoires
# =============================================================================

step "Répertoires applicatifs"

for d in "$INSTALL_DIR" "$DATA_DIR" "$LOG_DIR" "$BACKUP_DIR"; do
    if [[ ! -d "$d" ]]; then
        log "mkdir $d"
        run "mkdir -p \"$d\""
    fi
    run "chown -R $APP_USER:$APP_USER \"$d\""
done
run "chmod 750 \"$DATA_DIR\""
ok "Arborescence prête : $INSTALL_DIR, $DATA_DIR, $LOG_DIR, $BACKUP_DIR"

# =============================================================================
#  4. Code
# =============================================================================

step "Code source"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Repo existant — git pull"
    run "sudo -u $APP_USER git -C \"$INSTALL_DIR\" fetch --quiet origin"
    run "sudo -u $APP_USER git -C \"$INSTALL_DIR\" checkout --quiet $BRANCH"
    run "sudo -u $APP_USER git -C \"$INSTALL_DIR\" pull --quiet --ff-only"
else
    log "Clone $REPO_URL → $INSTALL_DIR"
    run "sudo -u $APP_USER git clone --quiet --branch $BRANCH \"$REPO_URL\" \"$INSTALL_DIR\""
fi
COMMIT=$(sudo -u $APP_USER git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "?")
ok "Code à jour (commit $COMMIT)"

# =============================================================================
#  5. Python venv + dépendances
# =============================================================================

step "Environnement Python"

if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
    log "Création du venv"
    run "sudo -u $APP_USER python3 -m venv \"$INSTALL_DIR/.venv\""
fi
log "Installation des dépendances (incluant gunicorn)"
run "sudo -u $APP_USER \"$INSTALL_DIR/.venv/bin/pip\" install --quiet --upgrade pip"
run "sudo -u $APP_USER \"$INSTALL_DIR/.venv/bin/pip\" install --quiet -r \"$INSTALL_DIR/requirements.txt\" gunicorn"
PY_VER=$("$INSTALL_DIR/.venv/bin/python" --version 2>&1 || echo "?")
ok "Python $PY_VER prêt."

# =============================================================================
#  6. Fichier d environnement
# =============================================================================

step "Fichier d'environnement \`$ENV_FILE\`"

if [[ ! -f "$ENV_FILE" ]]; then
    log "Génération de $ENV_FILE avec un AUBEDRONISTE_SECRET aléatoire"
    SECRET=$("$INSTALL_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')
    run "cat > \"$ENV_FILE\" <<EOF
# AubeDroniste — production environment
# Généré par deploy.sh le \$(date -Iseconds)

AUBEDRONISTE_HOST=127.0.0.1
AUBEDRONISTE_PORT=$PORT
AUBEDRONISTE_DATA=$DATA_DIR
AUBEDRONISTE_SECRET=$SECRET
SITE_URL=https://$DOMAIN

# --- SMTP transactionnel (à remplir) ---
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=no-reply@aubeetoilee.com
SMTP_FROM_NAME=AubeDroniste
SMTP_TLS=1

# --- Stripe Connect (mode FAKE actif tant que ces 3 sont vides) ---
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=

# Auto-libération escrow (jours sans validation client)
AUBEDRONISTE_AUTO_RELEASE_DAYS=7
EOF"
    run "chmod 600 \"$ENV_FILE\""
    run "chown root:$APP_USER \"$ENV_FILE\""
    warn "Édite $ENV_FILE pour remplir SMTP et Stripe, puis relance ce script."
else
    ok "$ENV_FILE existe — non modifié."
fi
run "chmod 640 \"$ENV_FILE\""

# =============================================================================
#  7. Database
# =============================================================================

step "Base de données"

if [[ ! -f "$DATA_DIR/aubedroniste.db" ]]; then
    log "Initialisation du schéma SQLite"
    run "sudo -u $APP_USER \"$INSTALL_DIR/.venv/bin/python\" -c \"
import os, sys
os.environ.setdefault('AUBEDRONISTE_DATA', '$DATA_DIR')
sys.path.insert(0, '$INSTALL_DIR')
import db
db.init_schema('$INSTALL_DIR/schema.sql')
print('schema OK')
\""
fi
DB_SIZE=$(stat -c '%s' "$DATA_DIR/aubedroniste.db" 2>/dev/null || echo "?")
ok "DB : $DATA_DIR/aubedroniste.db ($DB_SIZE octets)"

# =============================================================================
#  8. systemd unit
# =============================================================================

step "Service systemd"

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
log "Génération du fichier $UNIT_PATH"
run "cat > \"$UNIT_PATH\" <<EOF
[Unit]
Description=AubeDroniste — marketplace dronistes francophones
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/.venv/bin/gunicorn \\
  --workers 2 --threads 4 \\
  --bind 127.0.0.1:$PORT \\
  --access-logfile $LOG_DIR/access.log \\
  --error-logfile $LOG_DIR/error.log \\
  wsgi:app
Restart=on-failure
RestartSec=5
TimeoutStartSec=600
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR $LOG_DIR

[Install]
WantedBy=multi-user.target
EOF"

run "systemctl daemon-reload"
run "systemctl enable --quiet $SERVICE_NAME"
run "systemctl restart $SERVICE_NAME"
sleep 2

if systemctl is-active --quiet $SERVICE_NAME; then
    ok "Service $SERVICE_NAME actif."
else
    err "Service $SERVICE_NAME inactif. Logs : journalctl -u $SERVICE_NAME -n 30"
    [[ $DRY_RUN -eq 0 ]] && journalctl -u $SERVICE_NAME -n 20 --no-pager
    exit 1
fi

# =============================================================================
#  9. nginx
# =============================================================================

step "Configuration nginx"

NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"
log "Génération de $NGINX_CONF"
run "cat > \"$NGINX_CONF\" <<EOF
# AubeDroniste — reverse proxy vers gunicorn
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\\\$host\\\$request_uri; }
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header Referrer-Policy strict-origin-when-cross-origin;
    add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;

    client_max_body_size 12m;

    location /static/ {
        alias $INSTALL_DIR/static/;
        expires 30d;
        access_log off;
    }

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host              \\\$host;
        proxy_set_header X-Real-IP         \\\$remote_addr;
        proxy_set_header X-Forwarded-For   \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 60s;
    }
}
EOF"
run "ln -sfn \"$NGINX_CONF\" /etc/nginx/sites-enabled/${SERVICE_NAME}"
run "nginx -t -q"
run "systemctl reload nginx"
ok "nginx configuré et rechargé."

# =============================================================================
#  10. SSL via certbot
# =============================================================================

step "Certificat SSL Let's Encrypt"

if [[ $SKIP_CERT -eq 1 ]]; then
    warn "--skip-cert : certbot non exécuté."
elif [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
    ok "Cert déjà présent pour $DOMAIN — renouvellement par certbot.timer."
else
    log "Émission du certificat (certbot --nginx)"
    run "certbot --nginx --non-interactive --agree-tos --email \"$ADMIN_EMAIL\" --domain \"$DOMAIN\" --redirect"
fi

# =============================================================================
#  11. Cron : auto-release escrow + backup
# =============================================================================

step "Tâches cron"

CRON_FILE="/etc/cron.d/${SERVICE_NAME}"
log "Écriture de $CRON_FILE"
run "cat > \"$CRON_FILE\" <<EOF
# AubeDroniste — auto-release escrow (J+7) + backup quotidien

SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

0 4 * * * $APP_USER $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/scripts/release_stale_bookings.py >> $LOG_DIR/auto_release.log 2>&1
0 3 * * * $APP_USER /usr/bin/sqlite3 $DATA_DIR/aubedroniste.db \".backup '$BACKUP_DIR/aubedroniste-\$(date +\\%F).db'\" && find $BACKUP_DIR -name 'aubedroniste-*.db' -mtime +30 -delete
EOF"
run "chmod 644 \"$CRON_FILE\""
ok "Cron en place : auto-release 04h00, backup 03h00 (rétention 30j)"

# =============================================================================
#  12. Healthcheck
# =============================================================================

step "Healthcheck"

if [[ $DRY_RUN -eq 0 ]]; then
    sleep 2
    INTERNAL=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/api/stats" || echo "000")
    if [[ "$INTERNAL" == "200" ]]; then
        ok "App répond sur http://127.0.0.1:$PORT (200)"
    else
        err "App répond $INTERNAL sur 127.0.0.1:$PORT"
    fi

    if [[ $SKIP_CERT -eq 0 && -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
        EXTERNAL=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/api/stats" || echo "000")
        if [[ "$EXTERNAL" == "200" ]]; then
            ok "App publiquement accessible : https://$DOMAIN (200)"
        else
            warn "Externe répond $EXTERNAL — vérifier DNS / nginx / firewall."
        fi
    fi
fi

# =============================================================================
#  Bilan
# =============================================================================

cat <<EOF

${GREEN}═══════════════════════════════════════════════════════════════════${NC}
  ${GREEN}AubeDroniste déployé.${NC}
${GREEN}═══════════════════════════════════════════════════════════════════${NC}

  URL publique     https://$DOMAIN
  Service systemd  systemctl status $SERVICE_NAME
  Logs             journalctl -u $SERVICE_NAME -f
  Données          $DATA_DIR
  Backups          $BACKUP_DIR
  Env file         $ENV_FILE
  Code             $INSTALL_DIR  (commit $COMMIT)

  ${YELLOW}À faire ensuite :${NC}
  • Éditer $ENV_FILE pour remplir SMTP_HOST et les clés Stripe (sk_live_*)
    puis : sudo systemctl restart $SERVICE_NAME
  • Configurer le webhook Stripe sur https://$DOMAIN/stripe/webhook
  • Inscrire l'URL dans AubeStatus (port 5021) : https://$DOMAIN/api/stats
  • Lancer : sudo bash $INSTALL_DIR/deploy/healthcheck.sh

EOF
