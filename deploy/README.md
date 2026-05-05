# Déploiement AubeDroniste

Cible : un sous-domaine `droniste.aubeetoilee.com`, derrière nginx + Let's Encrypt,
avec gunicorn comme serveur d'application et systemd pour le cycle de vie.

## 1. Prérequis

- Linux (Debian/Ubuntu testé), Python 3.11+
- Un user système non-root pour faire tourner l'app (ex. `aube`)
- nginx + certbot installés
- Compte SMTP (Postmark, Mailjet, AubeMail interne, ou OVH)

## 2. Installation

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

## 9. Mise à jour

```bash
cd /srv/aubedroniste
sudo -u aube git pull
sudo -u aube .venv/bin/pip install -r requirements.txt
sudo systemctl restart aubedroniste
```
