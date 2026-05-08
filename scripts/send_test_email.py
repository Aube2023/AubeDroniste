"""Envoi d'un email de test pour valider la chaine SMTP / templates.

Usage :
    python scripts/send_test_email.py welcome demo@aubemail.com
    python scripts/send_test_email.py new_bid demo@aubemail.com
    python scripts/send_test_email.py bid_accepted demo@aubemail.com
    python scripts/send_test_email.py new_message demo@aubemail.com

Variables d'env honorees : SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
Si SMTP_HOST est vide, l'email est ecrit dans data/mail/*.eml.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# En prod Linux, charger /etc/aubepilot.env si present (systemd le fait
# pour le service mais pas pour les scripts standalone).
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
import mailer        # noqa: E402

DEMO_USER = {
    "id": 1, "username": "demo.pilote",
    "email": "demo@aubemail.com", "full_name": "Sophie Tremblay",
    "role": "pilot",
}
DEMO_CLIENT = {"id": 9, "username": "demo.client",
               "email": "client@aubemail.com", "full_name": "Imane Cherif",
               "role": "client"}
DEMO_PILOT = {"id": 1, "full_name": "Sophie Tremblay"}
DEMO_MISSION = {"id": 42, "title": "Captation aérienne — riad à Marrakech",
                "country": "Maroc", "city": "Marrakech"}
DEMO_BID = {"price": 850.0, "currency": "EUR", "eta_hours": 4,
            "message": "J'opère un Mavic 3 Pro avec assurance RC, 6 ans d'expérience."}
DEMO_BOOKING = {"id": 17, "agreed_price": 850.0, "currency": "EUR",
                "platform_fee": 255.0}


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)

    template = sys.argv[1]
    to = sys.argv[2] if len(sys.argv) >= 3 else "demo@aubemail.com"

    with app.app_context():
        if template == "welcome":
            ok = mailer.send_welcome({**DEMO_USER, "email": to})
        elif template == "new_bid":
            ok = mailer.send_new_bid(
                client={**DEMO_CLIENT, "email": to},
                mission=DEMO_MISSION, bid=DEMO_BID, pilot=DEMO_PILOT,
            )
        elif template == "bid_accepted":
            ok = mailer.send_bid_accepted(
                pilot={"id": 1, "email": to, "full_name": "Sophie Tremblay"},
                mission=DEMO_MISSION, booking=DEMO_BOOKING, client=DEMO_CLIENT,
            )
        elif template == "new_message":
            ok = mailer.send_new_message(
                recipient={"id": 1, "email": to, "full_name": "Sophie"},
                sender={"id": 9, "full_name": "Imane Cherif"},
                mission=DEMO_MISSION,
                body="Bonjour, êtes-vous disponible le 15 mai pour un tournage de 4 h ?",
            )
        else:
            print(f"template inconnu: {template}"); sys.exit(2)

    print("envoi:", "OK" if ok else "ECHEC")
    print("(si SMTP_HOST est vide, regardez dans data/mail/*.eml)")


if __name__ == "__main__":
    main()
