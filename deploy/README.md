# Déploiement AubeDroniste

Cible : un sous-domaine `droniste.aubeetoilee.com`, derrière nginx + Let's Encrypt,
avec gunicorn comme serveur d'application et systemd pour le cycle de vie.

## 🚀 Installation en une commande (recommandé)

Sur ton serveur Debian/Ubuntu fraîchement créé :

```bash
# 1. DNS pointe déjà droniste.aubeetoilee.com vers ton IP (record A)
# 2. Sur le serveur :
curl -fsSL https://raw.githubusercontent.com/Aube2023/AubeDroniste/main/deploy/deploy.sh -o /tmp/deploy.sh
sudo bash /tmp/deploy.sh
```

Ça fait **tout** : user système, dépendances, clone, venv, env file avec
secret aléatoire, DB, systemd, nginx, SSL Let's Encrypt, cron auto-release,
cron backup, healthcheck final.

Le script est **idempotent** — re-lance-le après `git pull` ou si tu
modifies un fichier de config, il convergera vers l'état attendu.

### Variables d'environnement honorées

| Variable | Défaut | À surcharger ? |
|---|---|---|
| `DOMAIN` | `droniste.aubeetoilee.com` | si autre domaine |
| `APP_USER` | `aube` | si user existant |
| `INSTALL_DIR` | `/srv/aubedroniste` | rare |
| `DATA_DIR` | `/var/lib/aubedroniste` | rare |
| `ADMIN_EMAIL` | `no-reply@aubeetoilee.com` | pour Let's Encrypt |
| `BRANCH` | `main` | pour staging |

Exemple :
```bash
sudo DOMAIN=staging.aubeetoilee.com BRANCH=develop bash deploy/deploy.sh
```

### Après la première installation

L'env file `/etc/aubedroniste.env` est créé avec les SMTP et Stripe **vides**.
L'app démarre déjà (mode FAKE Stripe + emails dumpés sur disque), mais
tu dois remplir :

```bash
sudo nano /etc/aubedroniste.env
# remplir SMTP_HOST, SMTP_USER, SMTP_PASSWORD
# remplir STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET
sudo systemctl restart aubedroniste
```

### Mises à jour

```bash
sudo bash /srv/aubedroniste/deploy/update.sh
```

Pull, réinstalle deps si requirements.txt a bougé, restart, healthcheck.

### Vérification

```bash
sudo bash /srv/aubedroniste/deploy/healthcheck.sh
```

12 contrôles : systemd, nginx, DNS, HTTPS, cert, headers de sécurité, CSRF,
env file, dev_passwords absent, cron, DB.

---

## Installation manuelle (si tu préfères contrôler chaque étape)

### 1. Prérequis

- Linux (Debian/Ubuntu testé), Python 3.11+
- Un user système non-root pour faire tourner l'app (ex. `aube`)
- nginx + certbot installés
- Compte SMTP (Postmark, Mailjet, AubeMail interne, ou OVH)

### 2. Installation pas-à-pas

```bash
sudo mkdir -p /srv/aubedroniste
sudo chown aube:aube /srv/aubedroniste
sudo -u aube git clone https://github.com/Aube2023/AubeDroniste.git /srv/aubedroniste
cd /srv/aubedroniste
sudo -u aube python3 -m venv .venv
sudo -u aube .venv/bin/pip install -r requirements.txt gunicorn
sudo -u aube .venv/bin/python scripts/seed.py   # (optionnel) données démo
```

## 3. Variables d'environnement

Créer `/etc/aubedroniste.env` (chmod 600) :

```ini
AUBEDRONISTE_PORT=5034
AUBEDRONISTE_HOST=127.0.0.1
AUBEDRONISTE_DATA=/var/lib/aubedroniste
AUBEDRONISTE_SECRET=<secret long aleatoire>
SITE_URL=https://droniste.aubeetoilee.com

# SMTP transactionnel
SMTP_HOST=smtp.aubemail.com
SMTP_PORT=587
SMTP_USER=no-reply@aubemail.com
SMTP_PASSWORD=<mdp>
SMTP_FROM=no-reply@aubeetoilee.com
SMTP_FROM_NAME=AubeDroniste
SMTP_TLS=1

# Stripe Connect (sans cle = mode FAKE pour demo)
STRIPE_SECRET_KEY=sk_live_xxxxxxxx
STRIPE_PUBLISHABLE_KEY=pk_live_xxxxxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxx
```

### Stripe Connect en prod

1. Sur **stripe.com → Settings → Connect** : activer Connect, choisir
   "platform" et le branding (logo, nom AubeDroniste).
2. Récupérer les clés API dans **Developers → API keys**.
3. Créer un endpoint webhook dans **Developers → Webhooks** pointant
   vers `https://droniste.aubeetoilee.com/stripe/webhook`. Cocher les
   events :
   - `checkout.session.completed`
   - `account.updated`
   - `charge.refunded`
4. Copier le webhook signing secret dans `STRIPE_WEBHOOK_SECRET`.

### Cron auto-release (escrow J+7)

Ajouter au crontab du user `aube` :

```cron
0 4 * * * /srv/aubedroniste/.venv/bin/python /srv/aubedroniste/scripts/release_stale_bookings.py >> /var/log/aubedroniste/auto_release.log 2>&1
```

## 4. systemd

Copier `aubedroniste.service` dans `/etc/systemd/system/` :

```bash
sudo cp deploy/aubedroniste.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aubedroniste
sudo journalctl -u aubedroniste -f      # logs en direct
```

## 5. nginx + Let's Encrypt

Copier `nginx.conf.example` dans `/etc/nginx/sites-available/aubedroniste`,
créer le symlink dans `sites-enabled`, recharger, puis :

```bash
sudo certbot --nginx -d droniste.aubeetoilee.com
```

## 6. Test post-déploiement

```bash
curl -s https://droniste.aubeetoilee.com/api/stats | jq
curl -s https://droniste.aubeetoilee.com/api/country-breakdown | jq '.[:3]'
```

## 7. Backups

`cron` du user `aube` :

```cron
0 4 * * * sqlite3 /var/lib/aubedroniste/aubedroniste.db ".backup '/var/backups/aubedroniste-$(date +\%F).db'"
```

## 8. Monitoring

Inscrire l'URL dans **AubeStatus** (port 5021) :
- endpoint : `https://droniste.aubeetoilee.com/api/stats`
- intervalle : 60 s

## 9. Check-list sécurité avant ouverture publique

- [ ] `AUBEDRONISTE_SECRET` posé (généré via `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
- [ ] `SITE_URL=https://droniste.aubeetoilee.com` (avec `https://` — sinon HSTS et cookies `secure` ne s'activent pas)
- [ ] Cert Let's Encrypt valide (`curl -I https://droniste.aubeetoilee.com/`)
- [ ] `FLASK_DEBUG` **non posé** (debug = exécution de code à distance)
- [ ] User systemd `aube` non-root, `chmod 600 /etc/aubedroniste.env`
- [ ] `.dev_passwords` **n'est pas** sur le serveur (vérifier avec `find /srv/aubedroniste -name .dev_passwords`)
- [ ] PAM activé : `getent passwd <user>` retourne le user AubeMail
- [ ] Stripe LIVE configuré, webhook posé avec son `whsec_...`
- [ ] `curl -I https://droniste.aubeetoilee.com/` retourne `Strict-Transport-Security`, `X-Frame-Options: DENY`, CSP
- [ ] `curl -X POST https://droniste.aubeetoilee.com/inscription` retourne **403** (CSRF refusé)
- [ ] `nmap -sV droniste.aubeetoilee.com` : seuls 80 + 443 publics (5034 reste sur 127.0.0.1)
- [ ] Logs nginx ne contiennent pas de mots de passe (`grep -i password /var/log/nginx/access.log`)

### Générer un AUBEDRONISTE_SECRET solide

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
# colle dans /etc/aubedroniste.env
```

## 10. Mise à jour

```bash
cd /srv/aubedroniste
sudo -u aube git pull
sudo -u aube .venv/bin/pip install -r requirements.txt
sudo systemctl restart aubedroniste
```
