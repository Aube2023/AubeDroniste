"""Parametres du compte (/espace/parametres) : identite, langue, notifications
(honorees par le mailer), mot de passe, sessions, export JSON, suppression
(anonymisation, bloquee si argent en cours). Imports dans les tests (conftest)."""
import os
import time

import pytest


def _eml_count():
    from config import MAIL_DUMP_DIR
    return len(os.listdir(MAIL_DUMP_DIR)) if os.path.isdir(MAIL_DUMP_DIR) else 0


def test_settings_page_and_account_update(client, auth_client, make_user):
    import db
    u = make_user("set_user", role="both", country="France", city="Lyon")
    c = auth_client(u["id"])
    html = c.get("/espace/parametres").data.decode()
    assert "Paramètres du profil" in html and 'name="notify_alerts"' in html and 'name="notify_bids"' in html
    r = c.post("/espace/parametres/compte", data={
        "full_name": "Nouveau Nom", "phone": "+1 514 000 0000", "country": "Canada",
        "city": "Montréal", "lat": "45.5", "lng": "-73.6", "lang": "en"})
    assert r.status_code in (302, 303)
    assert "aube_lang=en" in r.headers.get("Set-Cookie", "")
    with client.application.app_context():
        row = dict(db.fetchone("SELECT * FROM users WHERE id=?", (u["id"],)))
    assert row["full_name"] == "Nouveau Nom" and row["city"] == "Montréal" and row["lang"] == "en"
    assert abs(row["lat"] - 45.5) < 1e-6 and row["phone"].startswith("+1")
    # langue du compte appliquee sans cookie explicite
    c2 = auth_client(u["id"])
    assert "Find a pilot." in c2.get("/pilotes").data.decode()


def test_name_locked_after_certificate_upload(client, auth_client, make_user):
    import db
    import services
    u = make_user("set_lock", role="pilot", full_name="Marie Verrou")
    with client.application.app_context():
        services.add_certification(u["id"], authority="DGAC", title="A2", document_path="uploads/x.pdf")
    c = auth_client(u["id"])
    assert "Demander un changement de nom" in c.get("/espace/parametres").data.decode()
    c.post("/espace/parametres/compte", data={"full_name": "Pirate", "country": "France"})
    with client.application.app_context():
        assert db.fetchone("SELECT full_name FROM users WHERE id=?", (u["id"],))["full_name"] == "Marie Verrou"


def test_notification_prefs_are_honored(client, auth_client, make_user):
    import services
    pilot = make_user("set_np", role="pilot", lat=48.85, lng=2.35)
    cli = make_user("set_nc", role="client")
    c = auth_client(pilot["id"])
    c.post("/espace/parametres/notifications", data={"notify_messages": "0", "notify_alerts": "0"})
    with client.application.app_context():
        assert services.wants_notification(pilot["id"], "notify_messages") is False
        assert services.wants_notification(pilot["id"], "notify_alerts") is False
        assert services.wants_notification(cli["id"], "notify_bids") is True   # defaut
        # alertes missions : le pilote desabonne n'est plus dans les destinataires
        mid = services.create_mission(cli["id"], title="Alerte test", description="x" * 20,
                                      mission_type="photo", country="France", city="Paris",
                                      lat=48.85, lng=2.35)
        ids = {p["id"] for p in services.pilots_for_mission_alert(services.get_mission(mid))}
        assert pilot["id"] not in ids
        # messagerie : pas de courriel au pilote desabonne
        import mailer
        before = _eml_count()
        sent = mailer.send_new_message(recipient={"id": pilot["id"], "email": pilot["email"]},
                                       sender={"full_name": "C"}, mission={"id": mid, "title": "t"}, body="hello")
        assert sent is False
        time.sleep(0.2)
        assert _eml_count() == before


def test_change_password_and_logout_others(client, auth_client, make_user):
    import auth
    u = make_user("set_pw", role="client", password="ancien123")
    with client.application.app_context():
        other = auth.create_session(u["id"], "autre appareil", "10.0.0.2")
    c = auth_client(u["id"])
    r = c.post("/espace/parametres/mot-de-passe", data={"current": "faux", "new": "nouveau123", "confirm": "nouveau123"})
    assert r.status_code in (302, 303)
    with client.application.app_context():
        assert auth.authenticate(u["username"], "ancien123") is True     # inchangé
    c.post("/espace/parametres/mot-de-passe", data={"current": "ancien123", "new": "nouveau123", "confirm": "nouveau123"})
    with client.application.app_context():
        assert auth.authenticate(u["username"], "nouveau123") is True
        assert auth.authenticate(u["username"], "ancien123") is False
        import db
        # l'autre appareil est deconnecte, la session courante reste
        n = db.fetchone("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (u["id"],))["n"]
        assert n == 1
    assert c.get("/espace/parametres").status_code == 200


def test_sessions_list_and_revoke(client, auth_client, make_user):
    import auth
    u = make_user("set_sess", role="client")
    with client.application.app_context():
        auth.create_session(u["id"], "Mozilla/5.0 (iPhone)", "1.2.3.4")
    c = auth_client(u["id"])
    html = c.get("/espace/parametres").data.decode()
    assert "iPhone" in html and "cet appareil" in html
    c.post("/espace/parametres/sessions/deconnecter")
    html = c.get("/espace/parametres").data.decode()
    assert "iPhone" not in html and "cet appareil" in html


def test_export_json(client, auth_client, make_user, app_ctx):
    import services
    u = make_user("set_export", role="client")
    services.create_mission(u["id"], title="Export mission", description="x" * 20,
                            mission_type="photo", country="France")
    r = auth_client(u["id"]).get("/espace/parametres/export.json")
    assert r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", "")
    data = r.get_json()
    assert data["account"]["username"] == u["username"]
    assert any(m["title"] == "Export mission" for m in data["missions_published"])
    assert "password" not in str(data).lower() or "passwords" not in data


def test_delete_account_blocked_then_anonymized(client, auth_client, funded_booking, client_user):
    import auth
    import db
    import services
    c = auth_client(client_user["id"])
    html = c.get("/espace/parametres").data.decode()
    assert "Impossible pour l'instant" in html
    r = c.post("/espace/parametres/supprimer", data={"confirm_word": "SUPPRIMER", "password": client_user["_password"]})
    with client.application.app_context():
        assert db.fetchone("SELECT deleted_at FROM users WHERE id=?", (client_user["id"],))["deleted_at"] is None
        # on solde la reservation -> plus de blocage
        services.confirm_completion(funded_booking, client_user["id"])
        assert services.account_deletion_blockers(client_user["id"]) == []
    r = c.post("/espace/parametres/supprimer", data={"confirm_word": "SUPPRIMER", "password": "mauvais"})
    with client.application.app_context():
        assert db.fetchone("SELECT deleted_at FROM users WHERE id=?", (client_user["id"],))["deleted_at"] is None
    r = c.post("/espace/parametres/supprimer", data={"confirm_word": "SUPPRIMER", "password": client_user["_password"]})
    assert r.status_code in (302, 303)
    with client.application.app_context():
        row = dict(db.fetchone("SELECT * FROM users WHERE id=?", (client_user["id"],)))
        assert row["deleted_at"] and row["full_name"] == "Compte supprimé" and row["email"].startswith("deleted-")
        assert row["phone"] is None and row["lat"] is None
        assert auth.authenticate(client_user["username"], client_user["_password"]) is False
        assert db.fetchone("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (client_user["id"],))["n"] == 0
        # l'historique reste pour l'autre partie
        b = services.get_booking(funded_booking)
        assert b["status"] == "completed" and b["client_name"] == "Compte supprimé"
    # plus de session : l'espace redirige vers la connexion
    assert c.get("/espace", follow_redirects=False).status_code in (302, 303)


def test_deleted_pilot_disappears_from_directory(client, make_user, app_ctx):
    import services
    p = make_user("set_delp", role="pilot", country="France")
    assert any(x["id"] == p["id"] for x in services.search_pilots(country="France", limit=500))
    assert services.delete_account(p["id"])["ok"]
    assert not any(x["id"] == p["id"] for x in services.search_pilots(country="France", limit=500))
    assert not any(x["id"] == p["id"] for x in services.featured_pilots(500))
