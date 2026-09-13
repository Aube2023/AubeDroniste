"""AubeCaptcha a l'inscription (/inscription), cles configurees.

En prod (depuis le 2026-09-13) AUBECAPTCHA_SITEKEY/SECRET sont posees : le
widget est affiche et un jeton valide, resolu sur pilot.aubeetoilee.com, est
exige. Le service distant est mocke (`aubecaptcha.verifier`) : on teste le
cablage, pas AubeCaptcha lui-meme.
"""
import pytest

import aubecaptcha
import config
import db


def _form(**over):
    data = {
        "username": "cap_user",
        "password": "demo1234",
        "confirm": "demo1234",
        "full_name": "Testeur Captcha",
        "role": "client",
        "country": "Canada",
    }
    data.update(over)
    return data


@pytest.fixture
def captcha_actif(monkeypatch):
    monkeypatch.setattr(config, "AUBECAPTCHA_SITEKEY", "aube-test-sitekey")
    monkeypatch.setattr(config, "AUBECAPTCHA_SECRET", "acs_test_secret")
    appels = []

    def faux_verifier(token, *, hote_attendu=None, **kw):
        appels.append((token, hote_attendu))
        return (token == "jeton-valide", "ok" if token == "jeton-valide" else "jeton_absent")

    monkeypatch.setattr(aubecaptcha, "verifier", faux_verifier)
    return appels


def _existe(client, username):
    with client.application.app_context():
        return db.fetchone("SELECT 1 FROM users WHERE username=?", (username,)) is not None


def test_widget_affiche_quand_sitekey_configuree(client, captcha_actif):
    html = client.get("/inscription").get_data(as_text=True)
    assert 'data-sitekey="aube-test-sitekey"' in html
    assert "captcha.aubeetoilee.com/widget.js" in html


def test_widget_absent_sans_sitekey(client):
    html = client.get("/inscription").get_data(as_text=True)
    assert "aubecaptcha" not in html


def test_sans_jeton_inscription_refusee(client, captcha_actif):
    r = client.post("/inscription", data=_form(username="cap_sans_jeton"))
    assert r.status_code == 200
    assert "anti-robot" in r.get_data(as_text=True)
    assert not _existe(client, "cap_sans_jeton")
    # Le jeton absent est bien soumis au verificateur, avec notre domaine.
    assert captcha_actif == [(None, "pilot.aubeetoilee.com")]


def test_jeton_refuse_par_le_service(client, captcha_actif):
    r = client.post("/inscription", data=_form(
        username="cap_mauvais", **{"aubecaptcha-token": "forge"}))
    assert r.status_code == 200
    assert not _existe(client, "cap_mauvais")


def test_message_refus_traduit(client, captcha_actif):
    client.set_cookie("aube_lang", "en", domain="localhost.localdomain")
    r = client.post("/inscription", data=_form(username="cap_en"))
    assert "Anti-robot check failed" in r.get_data(as_text=True)


def test_jeton_valide_inscription_acceptee(client, captcha_actif):
    r = client.post("/inscription", data=_form(
        username="cap_ok", **{"aubecaptcha-token": "jeton-valide"}))
    assert r.status_code in (302, 303)
    assert _existe(client, "cap_ok")


def test_sans_cles_le_captcha_reste_inerte(client, monkeypatch):
    # Filet : tant que les cles manquent (dev, tests), on ne bloque personne.
    monkeypatch.setattr(config, "AUBECAPTCHA_SITEKEY", "")
    monkeypatch.setattr(config, "AUBECAPTCHA_SECRET", "")
    r = client.post("/inscription", data=_form(username="cap_inerte"))
    assert r.status_code in (302, 303)
    assert _existe(client, "cap_inerte")
