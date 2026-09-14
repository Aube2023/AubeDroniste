"""Hygiene nocturne des donnees techniques (Loi 25, politique de confidentialite).

A executer en cron quotidien (voir /etc/cron.d/aubepilot en prod) :
    30 3 * * * /srv/aubepilot/.venv/bin/python /srv/aubepilot/scripts/purge_technical_data.py

- Sessions expirees : la table `sessions` garde l'adresse IP et le navigateur
  du login. Sans purge, ces lignes restaient pour toujours apres expiration.
- Formulaire de contact : l'adresse IP jointe au message (anti-abus) est
  effacee apres 90 jours ; le message reste pour le suivi du support.
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# En prod, charger /etc/aubepilot.env (sinon AUBEPILOT_SECRET manque)
ENV_FILE = "/etc/aubepilot.env"
if os.path.exists(ENV_FILE) and os.access(ENV_FILE, os.R_OK):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from app import app  # noqa: E402
import db            # noqa: E402
import services      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("purge_technical_data")


def main():
    with app.app_context():
        from flask import g
        g.db = db._connect()
        done = services.purge_technical_data()
        log.info("sessions expirées supprimées : %d ; IP de contact effacées : %d",
                 done["sessions"], done["contact_ips"])


if __name__ == "__main__":
    main()
