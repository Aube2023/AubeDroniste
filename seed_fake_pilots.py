#!/usr/bin/env python3
# =============================================================================
#  AubePilot — seed de faux pilotes (DEMO / preview)
# =============================================================================
#
#  Genere des pilotes fictifs pour visualiser l'annuaire. TOUT est marque par
#  le prefixe `seed_` (username + email) afin de pouvoir purger sans risque.
#
#  Usage :
#     python seed_fake_pilots.py            # cree 120 pilotes
#     python seed_fake_pilots.py 150        # cree 150 pilotes
#     python seed_fake_pilots.py --purge    # supprime TOUS les faux pilotes
#
#  Les FK sont en ON DELETE CASCADE : supprimer la ligne users suffit a
#  effacer profil, specialites, drones, territoires et certifications.
# =============================================================================

import random
import sys

import db
import config

PREFIX = "seed_"  # marqueur de purge

# --- Pools de donnees realistes ---------------------------------------------

FIRST_NAMES = [
    "Nicolas", "Lucas", "Léa", "Camille", "Hugo", "Manon", "Théo", "Inès",
    "Yanis", "Sofia", "Mehdi", "Amine", "Sarah", "Karim", "Nour", "Rayan",
    "Adam", "Lina", "Youssef", "Fatima", "Omar", "Aya", "Bilal", "Salma",
    "Ibrahima", "Aminata", "Moussa", "Awa", "Cheikh", "Fatou", "Mamadou",
    "Marie", "Pierre", "Julie", "Antoine", "Clara", "Maxime", "Chloé",
    "Gabriel", "Emma", "Raphaël", "Jade", "Louis", "Alice", "Arthur",
    "Élise", "Samuel", "Zoé", "Nathan", "Anaïs", "Walid", "Imane",
]

LAST_NAMES = [
    "Martin", "Bernard", "Dubois", "Thomas", "Robert", "Petit", "Durand",
    "Leroy", "Moreau", "Simon", "Laurent", "Lefebvre", "Michel", "Garcia",
    "Benali", "El Amrani", "Haddad", "Cherif", "Bouazizi", "Mansouri",
    "Toure", "Diallo", "Sow", "Ndiaye", "Traore", "Keita", "Camara",
    "Khoury", "Haddad", "Aoun", "Nguyen", "Fontaine", "Rousseau", "Girard",
    "Lambert", "Faure", "Mercier", "Blanc", "Guerin", "Boyer", "Roux",
]

# country -> [(city, lat, lng), ...]
GEO = {
    "France":         [("Paris", 48.8566, 2.3522), ("Lyon", 45.7640, 4.8357),
                       ("Marseille", 43.2965, 5.3698), ("Bordeaux", 44.8378, -0.5792),
                       ("Toulouse", 43.6047, 1.4442), ("Nantes", 47.2184, -1.5536),
                       ("Lille", 50.6292, 3.0573), ("Nice", 43.7102, 7.2620)],
    "Canada":         [("Montréal", 45.5017, -73.5673), ("Toronto", 43.6532, -79.3832),
                       ("Vancouver", 49.2827, -123.1207), ("Ottawa", 45.4215, -75.6972),
                       ("Calgary", 51.0447, -114.0719)],
    "Quebec":         [("Québec", 46.8139, -71.2080), ("Laval", 45.6066, -73.7124),
                       ("Gatineau", 45.4765, -75.7013), ("Sherbrooke", 45.4040, -71.8929)],
    "Belgique":       [("Bruxelles", 50.8503, 4.3517), ("Anvers", 51.2194, 4.4025),
                       ("Liège", 50.6326, 5.5797), ("Gand", 51.0543, 3.7174)],
    "Suisse":         [("Genève", 46.2044, 6.1432), ("Lausanne", 46.5197, 6.6323),
                       ("Zurich", 47.3769, 8.5417), ("Berne", 46.9480, 7.4474)],
    "Luxembourg":     [("Luxembourg", 49.6116, 6.1319), ("Esch-sur-Alzette", 49.4958, 5.9806)],
    "Maroc":          [("Casablanca", 33.5731, -7.5898), ("Marrakech", 31.6295, -7.9811),
                       ("Rabat", 34.0209, -6.8416), ("Tanger", 35.7595, -5.8340),
                       ("Agadir", 30.4278, -9.5981), ("Fès", 34.0181, -5.0078)],
    "Algerie":        [("Alger", 36.7538, 3.0588), ("Oran", 35.6971, -0.6308),
                       ("Constantine", 36.3650, 6.6147), ("Annaba", 36.9000, 7.7667)],
    "Tunisie":        [("Tunis", 36.8065, 10.1815), ("Sfax", 34.7406, 10.7603),
                       ("Sousse", 35.8256, 10.6369), ("Djerba", 33.8076, 10.8451)],
    "Senegal":        [("Dakar", 14.7167, -17.4677), ("Saint-Louis", 16.0179, -16.4896),
                       ("Thiès", 14.7910, -16.9359)],
    "Cote d'Ivoire":  [("Abidjan", 5.3600, -4.0083), ("Yamoussoukro", 6.8276, -5.2893),
                       ("Bouaké", 7.6906, -5.0303)],
    "Cameroun":       [("Douala", 4.0511, 9.7679), ("Yaoundé", 3.8480, 11.5021)],
    "Mali":           [("Bamako", 12.6392, -8.0029), ("Sikasso", 11.3176, -5.6665)],
    "Burkina Faso":   [("Ouagadougou", 12.3714, -1.5197), ("Bobo-Dioulasso", 11.1771, -4.2979)],
    "Niger":          [("Niamey", 13.5117, 2.1251), ("Zinder", 13.8053, 8.9881)],
    "Madagascar":     [("Antananarivo", -18.8792, 47.5079), ("Toamasina", -18.1492, 49.4023)],
    "Liban":          [("Beyrouth", 33.8938, 35.5018), ("Tripoli", 34.4367, 35.8497),
                       ("Saïda", 33.5571, 35.3729)],
}

CURRENCY_BY_COUNTRY = {
    "France": "EUR", "Belgique": "EUR", "Luxembourg": "EUR",
    "Suisse": "CHF", "Canada": "CAD", "Quebec": "CAD",
    "Maroc": "MAD", "Algerie": "DZD", "Tunisie": "TND",
    "Senegal": "XOF", "Cote d'Ivoire": "XOF", "Cameroun": "XOF",
    "Mali": "XOF", "Burkina Faso": "XOF", "Niger": "XOF",
    "Madagascar": "EUR", "Liban": "USD",
}

LANGS_BY_COUNTRY = {
    "France": "fr,en", "Belgique": "fr,nl,en", "Luxembourg": "fr,de,en",
    "Suisse": "fr,de,en", "Canada": "fr,en", "Quebec": "fr,en",
    "Maroc": "fr,ar,en", "Algerie": "fr,ar", "Tunisie": "fr,ar,en",
    "Senegal": "fr,en", "Cote d'Ivoire": "fr,en", "Cameroun": "fr,en",
    "Mali": "fr", "Burkina Faso": "fr", "Niger": "fr",
    "Madagascar": "fr,mg", "Liban": "ar,fr,en",
}

HEADLINES = [
    "Pilote certifié, images aériennes haut de gamme.",
    "Spécialiste inspection technique & thermographie.",
    "Cartographie RTK et photogrammétrie de précision.",
    "Vidéaste drone pour événements et mariages.",
    "Immobilier & patrimoine vus du ciel.",
    "FPV cinématique et cascades aériennes.",
    "Suivi de chantier BTP et calcul de volumes.",
    "Reportage et documentaire en zone difficile.",
    "Agriculture de précision et NDVI.",
    "Inspection éolienne, photovoltaïque et lignes HT.",
    "Disponible pour vos prises de vue 4K/6K.",
    "De la captation au livrable monté, clé en main.",
]

BIOS = [
    "Plusieurs années d'expérience en captation aérienne professionnelle. "
    "Matériel pro, assurance RC, livrables rapides.",
    "Pilote passionné, je couvre médias, immobilier et inspection technique. "
    "Devis sous 24 h.",
    "Cartographe drone, je produis orthophotos, modèles 3D et nuages de points "
    "pour vos relevés.",
    "Spécialisé dans l'inspection d'ouvrages et d'infrastructures énergétiques. "
    "Rapports détaillés.",
    "Réalisateur drone, je raconte vos projets en images. FPV, cinématique, "
    "post-production incluse.",
]

INSURERS = ["AXA", "Allianz", "Generali", "MAIF", "Groupama", "Helvetia", "La Mondiale"]


def _rng_drone():
    brand = random.choice(config.DRONE_BRANDS)
    models = config.DRONE_MODELS_BY_BRAND.get(brand) or []
    model = random.choice(models) if models else "Custom build"
    cat = random.choice([c for c, _ in config.DRONE_CATEGORIES])
    caps = random.sample(config.DRONE_CAPABILITIES,
                         k=random.randint(1, 4))
    return cat, brand, model, ",".join(caps)


def purge():
    with db.standalone() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE ?", (PREFIX + "%",)
        ).fetchone()[0]
        c.execute("DELETE FROM users WHERE username LIKE ?", (PREFIX + "%",))
    print(f"✓ {n} faux pilotes supprimés (cascade sur profils/drones/etc).")


def seed(count: int):
    random.seed(424242)  # deterministe
    countries = list(GEO.keys())
    mission_codes = [c for c, _ in config.MISSION_TYPES]
    authorities = [a for a, _ in config.LICENCE_AUTHORITIES]
    created = 0

    with db.standalone() as c:
        # point de depart : evite les collisions si on relance
        base = c.execute(
            "SELECT COALESCE(MAX(id), 0) FROM users"
        ).fetchone()[0]

        for i in range(1, count + 1):
            seq = base + i
            uname = f"{PREFIX}pil{seq:04d}"
            email = f"{uname}@aubemail.com"
            fn = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            country = random.choice(countries)
            city, clat, clng = random.choice(GEO[country])
            # leger jitter autour de la ville
            lat = round(clat + random.uniform(-0.08, 0.08), 5)
            lng = round(clng + random.uniform(-0.08, 0.08), 5)
            role = random.choices(["pilot", "both"], weights=[80, 20])[0]
            verified = 1 if random.random() < 0.45 else 0
            bio = random.choice(BIOS)

            cur = c.execute(
                "INSERT INTO users "
                "(username, email, full_name, phone, country, city, lat, lng, "
                " role, bio, is_verified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uname, email, fn, None, country, city, lat, lng,
                 role, bio, verified),
            )
            uid = cur.lastrowid

            # --- profil pilote ---
            ccy = CURRENCY_BY_COUNTRY.get(country, "EUR")
            hourly = random.choice([60, 80, 90, 110, 120, 150, 180, 220, 250])
            insurance = 1 if random.random() < 0.7 else 0
            c.execute(
                "INSERT INTO pilot_profiles "
                "(user_id, headline, years_experience, hourly_rate, daily_rate, "
                " currency, travel_radius_km, accepts_remote, insurance, "
                " insurance_company, is_available, languages, accepts_urgent, "
                " stripe_charges_enabled, stripe_payouts_enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, random.choice(HEADLINES), random.randint(0, 15),
                 hourly, hourly * random.choice([5, 6, 7]), ccy,
                 random.choice([30, 50, 80, 100, 150, 250]),
                 1 if random.random() < 0.4 else 0,
                 insurance,
                 random.choice(INSURERS) if insurance else None,
                 1 if random.random() < 0.85 else 0,
                 LANGS_BY_COUNTRY.get(country, "fr"),
                 1 if random.random() < 0.5 else 0,
                 1, 1),
            )

            # --- specialites (2 a 5) ---
            for code in random.sample(mission_codes, k=random.randint(2, 5)):
                c.execute(
                    "INSERT OR IGNORE INTO pilot_specialties "
                    "(pilot_user_id, mission_type) VALUES (?, ?)",
                    (uid, code),
                )

            # --- drones (1 a 3) ---
            for _ in range(random.randint(1, 3)):
                cat, brand, model, caps = _rng_drone()
                c.execute(
                    "INSERT INTO pilot_drones "
                    "(pilot_user_id, category, brand, model, capabilities, "
                    " flight_time_min) VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, cat, brand, model, caps, random.choice([20, 25, 30, 40, 45])),
                )

            # --- territoire principal ---
            c.execute(
                "INSERT OR IGNORE INTO pilot_territories "
                "(pilot_user_id, country, region) VALUES (?, ?, ?)",
                (uid, country, city),
            )

            # --- 1 certification (parfois verifiee) ---
            if random.random() < 0.8:
                c.execute(
                    "INSERT INTO pilot_certifications "
                    "(pilot_user_id, authority, title, reference, is_verified) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (uid, random.choice(authorities),
                     random.choice(["STS-01", "STS-02", "Open A2", "Part 107",
                                    "Catégorie spécifique", "Brevet avancé"]),
                     f"REF-{random.randint(10000, 99999)}",
                     verified),
                )

            created += 1

    print(f"✓ {created} faux pilotes créés (prefixe '{PREFIX}').")
    print(f"  Purge : python seed_fake_pilots.py --purge")


if __name__ == "__main__":
    if "--purge" in sys.argv:
        purge()
    else:
        n = 120
        for a in sys.argv[1:]:
            if a.isdigit():
                n = int(a)
        seed(n)
