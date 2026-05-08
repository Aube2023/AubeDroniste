"""Auto-libération des bookings non confirmés depuis N jours.

A executer en cron quotidien :

    0 4 * * * /srv/aubepilot/.venv/bin/python /srv/aubepilot/scripts/release_stale_bookings.py

Le client a 7 jours (configurable via AUBEPILOT_AUTO_RELEASE_DAYS) pour
valider la mission. Apres ce delai, on libere automatiquement les fonds
au pilote pour eviter qu'il soit bloque par un client absent.

Si le booking est en `disputed`, on ne libere PAS — le litige doit etre
resolu manuellement.
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
from config import AUTO_RELEASE_DAYS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("auto_release")


def main():
    with app.app_context():
        from flask import g
        g.db = db._connect()
        ids = services.stale_funded_bookings(AUTO_RELEASE_DAYS)
        log.info("trouvé %d booking(s) à libérer (>%dj sans validation)",
                 len(ids), AUTO_RELEASE_DAYS)

        for booking_id in ids:
            booking = services.get_booking(booking_id)
            if not booking:
                continue
            log.info("auto-release booking #%s (%s vers pilote %s)",
                     booking_id, booking["agreed_price"], booking["pilot_user_id"])
            ok = services.confirm_completion(booking_id, booking["client_user_id"])
            if not ok:
                log.warning("auto-release échec pour booking #%s", booking_id)

        g.db.close()


if __name__ == "__main__":
    main()
