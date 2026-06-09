"""Fonctionnalite PHARE : geo / "pilote de n'importe ou".

On verifie le comportement reel de la couche geo :
- db.haversine_km : distance infinie si une coordonnee manque.
- services.search_pilots : filtre pays, NON-EXCLUSION par defaut (la distance
  est calculee pour le tri mais n'exclut que si strict_radius), filtre
  disponibilite.
- services.pilots_for_mission_alert : un pilote accepts_remote est alerte quelle
  que soit la distance ; un pilote local hors de son rayon est exclu.

DB de test PARTAGEE : on n'asserte QUE sur les ids qu'on cree (jamais des
comptes globaux exacts).
"""
import db
import services


# Coordonnees de reference (cap sur le reel du code, pas sur des constantes).
PARIS = (48.8566, 2.3522)
TOULOUSE = (43.6045, 1.4440)   # ~590 km de Paris -> hors d'un rayon de 50 km


def _ids(rows):
    return {r["id"] for r in rows}


# --------------------------------------------------------------------------- #
# haversine
# --------------------------------------------------------------------------- #

def test_haversine_inf_si_coord_manquante():
    assert db.haversine_km(None, 2.0, 3.0, 4.0) == float("inf")
    assert db.haversine_km(1.0, None, 3.0, 4.0) == float("inf")
    # Sanity : deux points identiques -> 0 km, Paris<->Toulouse > 500 km.
    assert db.haversine_km(*PARIS, *PARIS) == 0.0
    assert db.haversine_km(*PARIS, *TOULOUSE) > 500


# --------------------------------------------------------------------------- #
# search_pilots : filtre pays
# --------------------------------------------------------------------------- #

def test_search_pilots_filtre_par_pays(app_ctx, make_user):
    fr = make_user("anywhere_fr", role="both", country="France",
                   city="Paris", lat=PARIS[0], lng=PARIS[1])
    sn = make_user("anywhere_sn", role="both", country="Senegal",
                   city="Dakar", lat=14.6928, lng=-17.4467)

    found = _ids(services.search_pilots(country="France"))
    assert fr["id"] in found
    # Le pilote Senegal (sans territoire France) ne doit PAS apparaitre.
    assert sn["id"] not in found

    # Reciproquement, le pilote Senegal apparait bien pour country='Senegal'.
    found_sn = _ids(services.search_pilots(country="Senegal"))
    assert sn["id"] in found_sn
    assert fr["id"] not in found_sn


# --------------------------------------------------------------------------- #
# search_pilots : NON-EXCLUSION par defaut, exclusion seulement si strict_radius
# --------------------------------------------------------------------------- #

def test_search_pilots_non_exclusion_sauf_strict_radius(app_ctx, make_user):
    near = make_user("anywhere_near", role="both", country="France",
                     city="Paris", lat=PARIS[0], lng=PARIS[1])
    far = make_user("anywhere_far", role="both", country="France",
                    city="Toulouse", lat=TOULOUSE[0], lng=TOULOUSE[1])

    # Sans strict_radius : les deux restent visibles (le lointain aussi).
    loose = _ids(services.search_pilots(lat=PARIS[0], lng=PARIS[1]))
    assert near["id"] in loose
    assert far["id"] in loose

    # Avec strict_radius + rayon 50 km : seul le proche subsiste.
    strict = _ids(services.search_pilots(
        lat=PARIS[0], lng=PARIS[1], strict_radius=True, radius_km=50))
    assert near["id"] in strict
    assert far["id"] not in strict


# --------------------------------------------------------------------------- #
# search_pilots : filtre disponibilite
# --------------------------------------------------------------------------- #

def test_search_pilots_indisponible_exclu(app_ctx, make_user):
    on = make_user("anywhere_on", role="both", country="France",
                   city="Paris", lat=PARIS[0], lng=PARIS[1])
    off = make_user("anywhere_off", role="both", country="France",
                    city="Paris", lat=PARIS[0], lng=PARIS[1])
    services.upsert_pilot_profile(off["id"], is_available=0)

    available = _ids(services.search_pilots(country="France", only_available=True))
    assert on["id"] in available
    assert off["id"] not in available

    # only_available=False : l'indisponible reapparait.
    everyone = _ids(services.search_pilots(country="France", only_available=False))
    assert off["id"] in everyone


# --------------------------------------------------------------------------- #
# pilots_for_mission_alert : accepts_remote vs rayon local
# --------------------------------------------------------------------------- #

def test_mission_alert_remote_inclus_local_hors_rayon_exclu(app_ctx, make_user):
    # Mission a Paris.
    mission = {"lat": PARIS[0], "lng": PARIS[1], "city": "Paris"}

    # Pilote lointain (Toulouse) qui accepte les missions hors zone -> alerte.
    remote = make_user("alert_remote", role="both", country="France",
                       city="Toulouse", lat=TOULOUSE[0], lng=TOULOUSE[1])
    services.upsert_pilot_profile(remote["id"], accepts_remote=1)

    # Pilote lointain (Toulouse) local, petit rayon -> hors zone, exclu.
    local_far = make_user("alert_local_far", role="both", country="France",
                          city="Toulouse", lat=TOULOUSE[0], lng=TOULOUSE[1])
    services.upsert_pilot_profile(
        local_far["id"], accepts_remote=0, travel_radius_km=30)

    # Pilote proche (Paris) local, petit rayon -> dans la zone, inclus.
    local_near = make_user("alert_local_near", role="both", country="France",
                           city="Paris", lat=PARIS[0], lng=PARIS[1])
    services.upsert_pilot_profile(
        local_near["id"], accepts_remote=0, travel_radius_km=30)

    alerted = _ids(services.pilots_for_mission_alert(mission))
    assert remote["id"] in alerted          # accepte le distant
    assert local_near["id"] in alerted      # local dans le rayon
    assert local_far["id"] not in alerted   # local hors rayon


def test_mission_alert_indisponible_exclu(app_ctx, make_user):
    mission = {"lat": PARIS[0], "lng": PARIS[1], "city": "Paris"}
    # Pilote proche mais indisponible : jamais alerte, meme accepts_remote.
    off = make_user("alert_off", role="both", country="France",
                    city="Paris", lat=PARIS[0], lng=PARIS[1])
    services.upsert_pilot_profile(off["id"], accepts_remote=1, is_available=0)

    assert off["id"] not in _ids(services.pilots_for_mission_alert(mission))
