"""Donnees de demonstration pour AubeDroniste.

Lance : `python seed.py` avec la DB initialisee. Idempotent : passe si
un compte demo existe deja.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import auth
import db
import services
from config import DB_PATH


def seed():
    if not os.path.exists(DB_PATH):
        from app import bootstrap_db
        bootstrap_db()

    with db.standalone() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM users")
        if cur.fetchone()["n"] >= 5:
            print("seed: deja peuple, skip")
            return

    samples = [
        # username, full_name, role, country, city, lat, lng
        ("amine.benali",   "Amine Benali",      "droniste", "Maroc",   "Casablanca",  33.5731, -7.5898),
        ("sophie.tremblay","Sophie Tremblay",   "droniste", "Canada",  "Montreal",    45.5017, -73.5673),
        ("yacine.haddad",  "Yacine Haddad",     "droniste", "Algerie", "Alger",       36.7538,  3.0588),
        ("linh.dupont",    "Linh Dupont",       "droniste", "France",  "Lyon",        45.7640,  4.8357),
        ("kofi.adjei",     "Kofi Adjei",        "droniste", "Cote d'Ivoire", "Abidjan", 5.3600, -4.0083),
        ("client.alpha",   "Marie Dubois",      "client",   "France",  "Paris",       48.8566,  2.3522),
        ("client.beta",    "Pierre Lavigne",    "client",   "Canada",  "Quebec",      46.8139, -71.2080),
        ("client.gamma",   "Imane Cherif",      "client",   "Tunisie", "Tunis",       36.8065, 10.1815),
    ]

    pwd = "demo"
    needed = []
    # Connexion principale Flask-style via standalone -- on simule g
    import flask
    fake_app = flask.Flask("seed")
    with fake_app.app_context():
        flask.g.db = db._connect()
        flask.g.db.execute("PRAGMA foreign_keys=ON;")

        for username, name, role, country, city, lat, lng in samples:
            row = db.fetchone("SELECT id FROM users WHERE username=?", (username,))
            if row:
                uid = row["id"]
            else:
                uid = auth.create_user(
                    username=username, password=pwd, full_name=name,
                    role=role, country=country, city=city, lat=lat, lng=lng,
                    send_welcome_email=False,
                )
            needed.append((uid, username, role, country))

        # Profils dronistes : on cree juste les comptes et la dispo par defaut.
        # Le pilote rempli lui-meme : headline, brevets, drones, specialites,
        # tarifs, langues, assurance, etc. via /espace/droniste.
        # On laisse les fiches vides exprès pour rester proche de la realite
        # d'un nouveau marketplace : profil progressivement complete par les
        # utilisateurs eux-memes.
        pilots = [(u, c) for (u, _, r, c) in needed if r == "droniste"]
        for uid, country in pilots:
            # Marque le pilote comme disponible et ajoute son pays comme
            # territoire d'operation principal — c'est le seul pre-remplissage.
            services.upsert_pilot_profile(uid, is_available=1)
            services.set_pilot_territories(uid, [{"country": country, "region": ""}])

        # Quelques missions ouvertes
        clients = [(u, c) for (u, _, r, c) in needed if r == "client"]
        if clients:
            uid_alpha = clients[0][0]
            uid_beta = clients[1][0] if len(clients) > 1 else clients[0][0]
            uid_gamma = clients[2][0] if len(clients) > 2 else clients[0][0]

            if not db.fetchone("SELECT id FROM missions LIMIT 1"):
                services.create_mission(
                    uid_alpha,
                    title="Visite virtuelle d'un loft a Paris",
                    description="Captation aerienne et interieure pour annonce immobiliere haut de gamme.",
                    mission_type="immobilier",
                    country="France", city="Paris", lat=48.85, lng=2.34,
                    budget_min=300, budget_max=700, currency="EUR",
                    duration_hours=3,
                    requires_insurance=1,
                )
                services.create_mission(
                    uid_beta,
                    title="Suivi de chantier - 3 mois - Quebec",
                    description="Survol mensuel d'un chantier de 4 immeubles, livrables orthophoto + 3D.",
                    mission_type="mapping",
                    country="Canada", city="Quebec", lat=46.81, lng=-71.21,
                    budget_min=2500, budget_max=4500, currency="CAD",
                    requires_capabilities=["rtk"],
                    requires_insurance=1,
                )
                services.create_mission(
                    uid_gamma,
                    title="Mariage a Sidi Bou Said",
                    description="Captation videon ceremonie et soiree, livrable clip 5 min + brut.",
                    mission_type="mariage",
                    country="Tunisie", city="Sidi Bou Said", lat=36.87, lng=10.34,
                    budget_min=600, budget_max=1100, currency="EUR",
                    is_urgent=1,
                )

        flask.g.db.commit()
        flask.g.db.close()

    print(f"seed: ok ({len(samples)} comptes, mdp '{pwd}')")


if __name__ == "__main__":
    seed()
