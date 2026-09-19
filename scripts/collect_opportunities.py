"""Collecte nocturne des opportunités (appels d'offres publics, données
ouvertes) et courriel hebdomadaire aux pilotes du pays.

Minuterie systemd sur le VPS (aubepilot-opportunites.timer, 05:20 UTC) :

    /srv/aubepilot/.venv/bin/python /srv/aubepilot/scripts/collect_opportunities.py

Le courriel part le lundi seulement (ou avec --digest), aux pilotes dont les
alertes sont activées, s'il y a eu de nouvelles fiches dans la semaine.
"""
import logging
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

ENV_FILE = "/etc/aubepilot.env"
if os.path.exists(ENV_FILE) and os.access(ENV_FILE, os.R_OK):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from app import app   # noqa: E402
import mailer         # noqa: E402
import opportunities  # noqa: E402
import services       # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("opportunites")


def main() -> int:
    report = opportunities.collect()
    log.info("collecte: %s", report)
    if "--digest" in sys.argv or date.today().weekday() == 0:
        with app.app_context():
            items = opportunities.recent_for_digest(7)
            if items:
                pilots = services.pilots_for_opportunity_digest("Canada")
                log.info("digest: %d fiche(s), %d destinataire(s)", len(items), len(pilots))
                log.info("digest: %d envoye(s)", mailer.send_opportunity_digest(pilots, items))
            else:
                log.info("digest: rien de nouveau cette semaine")
    return 0


if __name__ == "__main__":
    sys.exit(main())
