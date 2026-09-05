"""Confidentialite des fichiers /media et minimisation des API publiques.

Regressions verrouillees par ces tests :

1. /media est PUBLIC (aucune auth). Il ne doit servir QUE des medias publics
   (avatars, photos de drone, portfolio). Les fichiers PRIVES qui vivent dans
   le meme UPLOAD_DIR — documents de brevet (u<id>_cert_*), pieces d'identite
   des demandes de changement de nom (u<id>_namechange_*) et livrables payants
   (booking_<id>/*) — doivent renvoyer 404, meme si on connait le nom exact.
   (Avant correctif : GET /media/u1_cert_....pdf servait la piece a un anonyme.)

2. Les API JSON publiques /api/pilotes et /api/missions ne doivent pas exposer
   de donnees personnelles brutes : nom complet -> masque, coordonnees exactes
   -> floutees, username/bio/nom du client/adresse -> retires (Loi 25).

DB partagee : on n'asserte que l'etat/l'appartenance des ids qu'on cree.
"""
import json
import os as _os
import tempfile as _tempfile

_os.environ.setdefault(
    "AUBEPILOT_DATA",
    _tempfile.mkdtemp(prefix="aubepilot-test-media-"),
)

import os

import auth
import services
from config import UPLOAD_DIR


def _write(rel: str, content: str = "x") -> None:
    path = os.path.join(UPLOAD_DIR, rel)
    os.makedirs(os.path.dirname(path) or UPLOAD_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# /media : les fichiers prives ne sortent pas (404), les publics oui (200)
# ---------------------------------------------------------------------------

def test_media_refuses_certification_document(client):
    _write("u1_cert_1699999999.pdf", "BREVET PRIVE")
    assert client.get("/media/u1_cert_1699999999.pdf").status_code == 404


def test_media_refuses_namechange_id_scan(client):
    _write("u1_namechange_1699999999.pdf", "PIECE IDENTITE")
    assert client.get("/media/u1_namechange_1699999999.pdf").status_code == 404


def test_media_refuses_booking_deliverable(client):
    _write("booking_1/1_livraison.zip", "LIVRABLE PAYANT")
    assert client.get("/media/booking_1/1_livraison.zip").status_code == 404


def test_media_refuses_private_even_for_authenticated_outsider(
    client, auth_client, make_user
):
    """Un utilisateur connecte mais tiers n'obtient rien de plus par /media."""
    _write("u1_cert_2699999999.pdf", "BREVET PRIVE")
    outsider = make_user("media_outsider", role="both")
    c = auth_client(outsider["id"])
    assert c.get("/media/u1_cert_2699999999.pdf").status_code == 404


def test_media_serves_public_avatar(client):
    _write("avatar_u1_123.png", "AVATAR")
    assert client.get("/media/avatar_u1_123.png").status_code == 200


def test_media_serves_public_portfolio(client):
    _write("portfolio_u1/1_wedding.png", "PORTFOLIO")
    assert client.get("/media/portfolio_u1/1_wedding.png").status_code == 200


# ---------------------------------------------------------------------------
# /api/pilotes : nom masque, coords floutees, champs internes retires
# ---------------------------------------------------------------------------

def test_api_pilotes_masks_pii(app_ctx, client, make_user):
    u = make_user("apitest_pilot", role="both", country="Canada",
                  city="Montreal", lat=45.501234, lng=-73.567891,
                  full_name="Amelie Tremblay")
    services.upsert_pilot_profile(u["id"], headline="Inspections", is_available=1)
    data = json.loads(client.get("/api/pilotes?country=Canada").data)
    p = next((x for x in data["pilots"] if x["id"] == u["id"]), None)
    assert p is not None
    # Nom masque : jamais le nom complet brut.
    assert p["full_name"] == services.public_name(u)
    assert p["full_name"] != u["full_name"]
    # Coordonnees floutees a la grille ~11 km (1 decimale), pas la valeur exacte.
    assert p["lat"] == round(45.501234, 1)
    assert p["lat"] != 45.501234
    # Champs internes retires.
    assert "username" not in p
    assert "bio" not in p


# ---------------------------------------------------------------------------
# /api/missions : nom du client et adresse retires, coords floutees
# ---------------------------------------------------------------------------

def test_api_missions_hides_client_and_address(app_ctx, client, make_user):
    cu = make_user("apitest_client", role="client", country="Canada")
    mid = services.create_mission(
        cu["id"], title="Survol usine", description="desc",
        mission_type="inspection", country="Canada", city="Laval",
        lat=45.6122, lng=-73.71234, address="123 rue Secrete",
    )
    data = json.loads(client.get("/api/missions?country=Canada").data)
    m = next((x for x in data["missions"] if x["id"] == mid), None)
    assert m is not None
    assert "client_name" not in m
    assert "address" not in m
    assert m["lat"] == round(45.6122, 2)
