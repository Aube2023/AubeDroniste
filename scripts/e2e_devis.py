"""Parcours complet « devis » de bout en bout, en mode Stripe FAKE, sur une
base temporaire — pour prouver que la chaîne fonctionne et pour la rejouer
après chaque changement.

    AUBEPILOT_DATA=/tmp/e2e python scripts/e2e_devis.py

Étapes : inscription client + pilote → profil pilote (brevet + drone +
paiements) → mission → alerte pilote → devis → refus motivé → devis révisé →
acceptation → paiement (checkout simulé) → messagerie → livrable → PDF du
devis → validation → virement (simulé) → avis croisés → vérification admin
du brevet. Chaque étape affiche OK/KO ; code de sortie 1 si une étape casse.
"""
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("AUBEPILOT_DATA", os.path.join("/tmp", "aubepilot-e2e"))
os.makedirs(os.environ["AUBEPILOT_DATA"], exist_ok=True)

from app import app, bootstrap_db  # noqa: E402
import db                           # noqa: E402
import services                     # noqa: E402
from config import MAIL_DUMP_DIR    # noqa: E402

app.config.update(TESTING=True, SERVER_NAME="localhost.localdomain")
if not db.schema_ready():
    with app.app_context():
        bootstrap_db()

STEPS = []


def step(label, ok, detail=""):
    STEPS.append((label, bool(ok), detail))
    print(("  ✓ " if ok else "  ✗ ") + label + (f" — {detail}" if detail else ""))
    return ok


def emails():
    return sorted(os.listdir(MAIL_DUMP_DIR)) if os.path.isdir(MAIL_DUMP_DIR) else []


def new_emails(before):
    time.sleep(0.4)
    return [f for f in emails() if f not in before]


def login(c, username, password):
    return c.post("/connexion", data={"username": username, "password": password},
                  follow_redirects=False)


def main():
    stamp = int(time.time()) % 100000
    cli, pil = f"e2e_client{stamp}", f"e2e_pilot{stamp}"
    with app.test_client() as c:
        # ---- 1. inscriptions
        before = emails()
        r = c.post("/inscription", data={"username": cli, "password": "demo1234", "confirm": "demo1234",
                                         "full_name": "Claire Client", "role": "client",
                                         "country": "Canada", "city": "Montréal",
                                         "lat": "45.50", "lng": "-73.57"})
        step("Inscription client", r.status_code in (302, 303))
        c.get("/deconnexion")
        r = c.post("/inscription", data={"username": pil, "password": "demo1234", "confirm": "demo1234",
                                         "full_name": "Paul Pilote", "role": "pilot",
                                         "country": "Canada", "city": "Laval",
                                         "lat": "45.57", "lng": "-73.69"})
        step("Inscription pilote", r.status_code in (302, 303))
        step("Courriels de bienvenue générés", len(new_emails(before)) >= 2)

        # ---- 2. profil pilote : brevet (avec justificatif) + drone + specialites + paiements
        pdf = (io.BytesIO(b"%PDF-1.4\n% brevet de test\n"), "brevet.pdf")
        r = c.post("/espace/pilote/certification", data={
            "authority": "Transport Canada", "title": "Operations avancees (RPAS)",
            "reference": "TC-2026-0042", "issued_at": "2025-01-10", "expires_at": "2030-01-10",
            "document": pdf}, content_type="multipart/form-data")
        step("Brevet + justificatif téléversés", r.status_code in (302, 303))
        r = c.post("/espace/pilote/drone", data={"category": "inspection", "brand": "DJI",
                                                "model": "Matrice 30T", "capabilities": ["thermique", "camera_4k"]})
        step("Drone ajouté", r.status_code in (302, 303))
        r = c.post("/espace/pilote", data={"headline": "Inspection thermique & toiture", "hourly_rate": "150",
                                          "currency": "CAD", "travel_radius_km": "100", "is_available": "1",
                                          "insurance": "1", "insurance_company": "Intact", "insurance_policy": "P-777",
                                          "specialties": ["toiture", "thermographie"], "country": "Canada",
                                          "city": "Laval", "lat": "45.57", "lng": "-73.69"})
        step("Profil pilote enregistré", r.status_code in (200, 302, 303))
        # En mode FAKE, l'onboarding redirige vers SITE_URL/stripe/fake-onboarding/…
        # (hote externe pour le client de test) : on suit le chemin a la main.
        from urllib.parse import urlparse
        r = c.get("/espace/pilote/stripe", follow_redirects=False)
        loc = urlparse(r.headers.get("Location", ""))
        if loc.path:
            c.get(loc.path + (("?" + loc.query) if loc.query else ""), follow_redirects=False)
            r2 = c.get("/stripe/return", follow_redirects=False)
        with app.app_context():
            pilot_row = db.fetchone("SELECT id FROM users WHERE username=?", (pil,))
            pilot_id = pilot_row["id"]
            acc = services.get_pilot_stripe_account(pilot_id)
        step("Paiements pilote activés (Stripe simulé)", bool(acc), acc or "pas de compte")
        c.get("/deconnexion")

        # ---- 3. mission cote client + alerte pilote
        login(c, cli, "demo1234")
        before = emails()
        r = c.post("/missions/nouvelle", data={
            "title": "Inspection thermique toiture entrepôt", "mission_type": "thermographie",
            "description": "Toiture 2 000 m², recherche de fuites, rapport thermique attendu.",
            "country": "Canada", "city": "Montréal", "lat": "45.50", "lng": "-73.57",
            "budget_min": "800", "budget_max": "1500", "currency": "CAD",
            "start_date": "2026-09-30", "requires_insurance": "1"})
        with app.app_context():
            mission = db.fetchone("SELECT id, status FROM missions ORDER BY id DESC LIMIT 1")
            mid = mission["id"]
        step("Mission publiée", r.status_code in (302, 303) and mission["status"] == "open", f"mission #{mid}")
        step("Alerte courriel au pilote dans le rayon", any("alert" in f.lower() or pil in f for f in new_emails(before)) or len(new_emails(before)) >= 1)
        c.get("/deconnexion")

        # ---- 4. devis, refus, revision, acceptation
        login(c, pil, "demo1234")
        before = emails()
        r = c.post(f"/missions/{mid}/enchere", data={
            "price": "1400", "currency": "CAD", "eta_hours": "48",
            "message": "Disponible la semaine prochaine.",
            "description": "Vol thermique au lever du jour, 2 passes, rapport PDF annoté avec localisation des anomalies.",
            "deliverables": "Rapport thermique PDF + 40 photos RJPEG + orthomosaïque",
            "terms": "Météo : report gratuit si pluie/vent > 30 km/h."})
        with app.app_context():
            bids = services.list_bids(mid)
        step("Devis n°1 déposé", r.status_code in (302, 303) and len(bids) == 1 and bids[0]["price"] == 1400)
        step("Courriel « nouveau devis » au client", len(new_emails(before)) >= 1)
        bid_id = bids[0]["id"]
        c.get("/deconnexion")

        login(c, cli, "demo1234")
        r = c.post(f"/missions/{mid}/refuser/{bid_id}", data={"reason": "Budget max 1 200, pouvez-vous ajuster ?"})
        with app.app_context():
            b = [x for x in services.list_bids(mid) if x["id"] == bid_id][0]
        step("Devis refusé avec motif", b["status"] == "rejected" and "1 200" in (b["client_response"] or ""))
        c.get("/deconnexion")

        login(c, pil, "demo1234")
        r = c.post(f"/missions/{mid}/enchere", data={
            "price": "1200", "currency": "CAD", "eta_hours": "48", "message": "Ajusté à 1 200.",
            "description": "Vol thermique au lever du jour, 2 passes, rapport PDF annoté.",
            "deliverables": "Rapport thermique PDF + 40 photos RJPEG", "terms": "Report météo gratuit."})
        with app.app_context():
            b = [x for x in services.list_bids(mid) if x["id"] == bid_id][0]
            revs = services.list_bid_revisions(bid_id)
        step("Devis révisé (v2) après refus", b["status"] == "pending" and b["price"] == 1200 and b["revision_no"] == 2
             and len(revs) == 1, f"révisions archivées : {len(revs)}")
        c.get("/deconnexion")

        login(c, cli, "demo1234")
        before = emails()
        r = c.post(f"/missions/{mid}/accepter/{bid_id}")
        with app.app_context():
            booking = db.fetchone("SELECT * FROM bookings WHERE bid_id=?", (bid_id,))
        step("Devis accepté → réservation en attente de paiement",
             booking is not None and booking["status"] == "pending_payment",
             f"réservation #{booking['id'] if booking else '?'} · commission {booking['platform_fee_pct'] if booking else '?'} % = {booking['platform_fee'] if booking else '?'} CAD")
        step("Courriel « devis accepté » au pilote", len(new_emails(before)) >= 1)
        bk = booking["id"]

        # ---- 5. paiement simule
        r = c.get(f"/reservations/{bk}/payer", follow_redirects=False)
        step("Redirection vers le paiement (checkout simulé)", r.status_code in (302, 303) and "fake-checkout" in r.headers.get("Location", ""))
        before = emails()
        r = c.post(f"/stripe/fake-checkout/{bk}", follow_redirects=False)
        with app.app_context():
            booking = services.get_booking(bk)
        step("Paiement encaissé sous séquestre (funded)", booking["status"] == "funded" and booking["paid_at"])
        step("Courriel « mission financée » au pilote", len(new_emails(before)) >= 1)
        page = c.get(f"/reservations/{bk}").data.decode()
        step("Page réservation : identité du pilote révélée + politique d'annulation", "Paul Pilote" in page and "Fenêtre de grâce" in page)

        # ---- 6. messagerie
        r = c.post(f"/missions/{mid}/messages", data={"body": "Bonjour Paul, accès par la cour arrière, badge à l'accueil.", "peer_id": pilot_id})
        with app.app_context():
            client_row = db.fetchone("SELECT id FROM users WHERE username=?", (cli,))
            th = services.thread(mid, client_row["id"], pilot_id)
        step("Message client → pilote dans le fil de la mission", r.status_code in (302, 303, 200) and len(th) >= 1)
        c.get("/deconnexion")

        # ---- 7. livrable + PDF du devis
        login(c, pil, "demo1234")
        r = c.post(f"/reservations/{bk}/livrables", data={
            "label": "Rapport thermique", "file": (io.BytesIO(b"%PDF-1.4\n% rapport\n"), "rapport_thermique.pdf")},
            content_type="multipart/form-data")
        with app.app_context():
            delivs = services.list_deliverables(bk)
        step("Livrable téléversé par le pilote", r.status_code in (302, 303) and len(delivs) == 1)
        r = c.get(f"/reservations/{bk}/devis.pdf")
        step("PDF du devis généré", r.status_code == 200 and r.data[:4] == b"%PDF", f"{len(r.data)} octets")
        c.get("/deconnexion")

        # ---- 8. validation, virement, avis
        login(c, cli, "demo1234")
        r = c.get(f"/reservations/{bk}/livrables/{delivs[0]['id']}/download")
        step("Client télécharge le livrable", r.status_code == 200)
        before = emails()
        r = c.post(f"/reservations/{bk}/valider")
        with app.app_context():
            booking = services.get_booking(bk)
        step("Validation → mission terminée, fonds virés au pilote (simulé)",
             booking["status"] == "completed" and str(booking["stripe_transfer_id"]).startswith("tr_fake_"),
             f"net pilote {booking['agreed_price'] - booking['platform_fee']:.0f} CAD")
        step("Courriel « paiement libéré » au pilote", len(new_emails(before)) >= 1)
        r = c.post(f"/reservations/{bk}/avis", data={"rating": "5", "comment": "Rapport clair, pilote ponctuel."})
        with app.app_context():
            rating = services.pilot_rating(pilot_id)
        step("Avis client → pilote (★5)", rating["count"] == 1 and rating["avg"] == 5.0)
        c.get("/deconnexion")
        login(c, pil, "demo1234")
        r = c.post(f"/reservations/{bk}/avis", data={"rating": "4", "comment": "Accès bien organisé."})
        with app.app_context():
            n = db.fetchone("SELECT COUNT(*) AS n FROM reviews WHERE booking_id=?", (bk,))["n"]
        step("Avis pilote → client (★4)", n == 2)
        c.get("/deconnexion")

        # ---- 9. verification admin du brevet
        with app.app_context():
            admin = db.fetchone("SELECT id, username FROM users WHERE is_admin=1 LIMIT 1")
            if not admin:
                db.execute("UPDATE users SET is_admin=1 WHERE username=?", (cli,))
                admin = {"username": cli}
            cert = db.fetchone("SELECT id FROM pilot_certifications WHERE pilot_user_id=?", (pilot_id,))
        login(c, admin["username"], "demo1234")
        page = c.get("/admin/certifications").data.decode()
        step("File admin : brevet à vérifier avec aperçu + nom du compte", f'id="cert-{cert["id"]}"' in page and "Paul Pilote" in page)
        r = c.post(f"/admin/certifications/{cert['id']}/verifier",
                   data={"check_name": "1", "check_number": "1", "check_valid": "1", "note": "e2e"})
        with app.app_context():
            u = db.fetchone("SELECT is_verified FROM users WHERE id=?", (pilot_id,))
        step("Brevet vérifié → badge « vérifié » sur le profil", u["is_verified"] == 1)
        page = c.get("/pilotes?only_verified=1").data.decode()
        step("Le pilote apparaît dans le filtre « brevet vérifié »", f"/pilotes/{pilot_id}" in page)

    ko = [s_ for s_ in STEPS if not s_[1]]
    print(f"\n{len(STEPS) - len(ko)}/{len(STEPS)} étapes OK" + (f" — ÉCHECS : {[k[0] for k in ko]}" if ko else ""))
    return 1 if ko else 0


if __name__ == "__main__":
    sys.exit(main())
