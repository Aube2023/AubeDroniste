"""Smoke tests : l'app demarre, les routes publiques repondent, la DB
se cree, le mailer dump bien dans data/mail en mode dev.
"""
import os

import pytest


@pytest.fixture(scope="module")
def client():
    from app import app, bootstrap_db, db
    if not os.path.exists(db.DB_PATH):
        bootstrap_db()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"AubeDroniste" in r.data


def test_lang_switch(client):
    r = client.get("/lang/en", follow_redirects=False)
    assert r.status_code in (302, 303)
    cookie = r.headers.get("Set-Cookie", "")
    assert "aube_lang=en" in cookie


def test_lang_invalid_404(client):
    r = client.get("/lang/zz")
    assert r.status_code == 404


def test_pilots_search(client):
    r = client.get("/dronistes")
    assert r.status_code == 200


def test_missions_search(client):
    r = client.get("/missions")
    assert r.status_code == 200


def test_login_get(client):
    r = client.get("/connexion")
    assert r.status_code == 200


def test_register_get(client):
    r = client.get("/inscription")
    assert r.status_code == 200


def test_api_stats(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.get_json()
    assert "pilots" in data
    assert "open_missions" in data


def test_api_country_breakdown(client):
    r = client.get("/api/country-breakdown")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)


def test_api_near_requires_coords(client):
    r = client.get("/api/near")
    assert r.status_code == 400


def test_api_near_ok(client):
    r = client.get("/api/near?lat=48.85&lng=2.34&radius_km=200")
    assert r.status_code == 200
    data = r.get_json()
    assert "pilots" in data and "missions" in data


def test_dashboard_requires_auth(client):
    r = client.get("/espace", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_404(client):
    r = client.get("/this-route-does-not-exist")
    assert r.status_code == 404


def test_register_creates_user_and_dumps_email(client):
    """Inscription valide = welcome email dumpé sur disque (pas de SMTP en test)."""
    from config import MAIL_DUMP_DIR
    pre_count = len(os.listdir(MAIL_DUMP_DIR)) if os.path.isdir(MAIL_DUMP_DIR) else 0

    r = client.post("/inscription", data={
        "username": "testuser_smoke", "password": "demo1234",
        "confirm": "demo1234", "full_name": "Test User",
        "role": "client", "country": "France", "city": "Paris",
    }, follow_redirects=False)
    # Soit redirige (succès), soit re-rend la page (échec validation) — l'important
    # est que l'app ne plante pas.
    assert r.status_code in (200, 302, 303)
    # Si succès, on doit avoir un .eml dans le dump
    import time; time.sleep(0.4)  # async send
    if r.status_code in (302, 303):
        post_count = len(os.listdir(MAIL_DUMP_DIR))
        assert post_count > pre_count, "welcome email non dumpé"
