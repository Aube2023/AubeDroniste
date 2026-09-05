"""Durcissement anti-robot de l'inscription (/inscription).

1. Honeypot : un robot qui remplit le champ cache `website_confirm` ne doit
   PAS creer de compte (on refuse en silence).
2. Un humain (champ honeypot vide) s'inscrit normalement -> compte cree.

AubeCaptcha n'est pas teste ici : sans SITEKEY/SECRET configures (cas des
tests), la verification est ignoree, l'inscription reste inchangee.
"""
import db


def _base_form(**over):
    data = {
        "username": "hp_user_ok",
        "password": "demo1234",
        "confirm": "demo1234",
        "full_name": "Testeur Honeypot",
        "role": "client",
        "country": "Canada",
    }
    data.update(over)
    return data


def test_honeypot_bloque_le_robot(client):
    # Robot : champ cache rempli -> aucun compte cree, pas de redirection.
    r = client.post("/inscription", data=_base_form(
        username="hp_robot", website_confirm="http://spam.example"))
    assert r.status_code == 200  # formulaire reaffiche, pas de 302 vers /espace
    with client.application.app_context():
        assert db.fetchone("SELECT 1 FROM users WHERE username='hp_robot'") is None


def test_humain_sinscrit_normalement(client):
    # Humain : honeypot vide (absent) -> compte cree.
    r = client.post("/inscription", data=_base_form(username="hp_humain"))
    assert r.status_code in (302, 303)
    with client.application.app_context():
        assert db.fetchone("SELECT 1 FROM users WHERE username='hp_humain'") is not None
