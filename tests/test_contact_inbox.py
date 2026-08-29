"""Boite de reception /contact : stockage avant courriel, back-office admin,
reponse par courriel, archivage. (Conventions conftest : imports dans les tests.)"""
import os
import time

import pytest


def _eml_count():
    from config import MAIL_DUMP_DIR
    return len(os.listdir(MAIL_DUMP_DIR)) if os.path.isdir(MAIL_DUMP_DIR) else 0


@pytest.fixture()
def admin_user(make_user, app_ctx):
    import db
    u = make_user("inbox_admin", role="client")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
    return u


def test_contact_submit_is_stored_even_if_smtp_down(client, monkeypatch):
    import mailer
    import services
    # SMTP "en panne" : l'envoi synchrone renvoie False, le message doit rester.
    monkeypatch.setattr(mailer, "_send_via_smtp", lambda msg, cfg: False)
    monkeypatch.setattr(mailer, "_smtp_config", lambda: {
        "host": "smtp.down.invalid", "port": 25, "user": "", "password": "",
        "from_email": "x@y.z", "from_name": "T", "tls": False})
    r = client.post("/contact", data={
        "name": "Marie Panne", "email": "marie@example.org", "topic": "client",
        "message": "Bonjour, message envoyé pendant que le SMTP est en panne.",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    with client.application.app_context():
        msgs = [m for m in services.list_contact_messages("new") if m["email"] == "marie@example.org"]
        assert msgs, "le message doit etre en base malgre le SMTP en panne"
        assert msgs[0]["notified_at"] is None          # courriel equipe NON parti
        assert msgs[0]["body"].startswith("Bonjour, message")


def test_contact_submit_marks_notified_when_mail_goes_out(client):
    import services
    r = client.post("/contact", data={
        "name": "Paul Ok", "email": "paul@example.org", "topic": "pilot",
        "message": "Message normal, dump local = envoi reussi.",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    with client.application.app_context():
        m = [m for m in services.list_contact_messages("new") if m["email"] == "paul@example.org"][0]
        assert m["notified_at"] is not None
        assert m["status"] == "new" and m["topic"]


def test_admin_inbox_requires_admin(client, auth_client, make_user):
    u = make_user("inbox_plain", role="client")
    assert auth_client(u["id"]).get("/admin/messages").status_code == 403
    assert client.get("/admin/messages").status_code in (302, 303, 401, 403)


def test_admin_inbox_lists_reply_and_archive(client, auth_client, admin_user):
    import services
    with client.application.app_context():
        mid = services.create_contact_message(
            name="Jeanne Q", email="jeanne@example.org", topic="Presse / média",
            body="Pouvez-vous m'envoyer un dossier de presse ?", ip="1.2.3.4")
        assert services.count_contact_messages("new") >= 1
    c = auth_client(admin_user["id"])
    html = c.get("/admin/messages").data.decode()
    assert "jeanne@example.org" in html and "dossier de presse" in html
    assert "Messages reçus" in html

    before = _eml_count()
    r = c.post(f"/admin/messages/{mid}/repondre", data={"reply": "Bonjour Jeanne, le voici en pièce jointe demain."})
    assert r.status_code in (302, 303)
    time.sleep(0.2)
    assert _eml_count() >= before + 1               # courriel de reponse dumpe
    with client.application.app_context():
        m = services.get_contact_message(mid)
        assert m["status"] == "replied" and m["reply_sent"] == 1
        assert m["replied_by"] == admin_user["id"]
        assert "pièce jointe" in m["reply_body"]
    from config import MAIL_DUMP_DIR
    newest = sorted(os.listdir(MAIL_DUMP_DIR))[-1]
    blob = open(os.path.join(MAIL_DUMP_DIR, newest), "rb").read()
    assert b"jeanne@example.org" in blob and b"Re: Presse" in blob

    html = c.get("/admin/messages?status=replied").data.decode()
    assert "courriel envoy" in html
    r = c.post(f"/admin/messages/{mid}/statut", data={"status": "archived", "back": "replied"})
    assert r.status_code in (302, 303)
    with client.application.app_context():
        assert services.get_contact_message(mid)["status"] == "archived"
    html = c.get("/admin/messages?status=archived").data.decode()
    assert "jeanne@example.org" in html


def test_admin_reply_keeps_text_when_smtp_down(client, auth_client, admin_user, monkeypatch):
    import mailer
    import services
    monkeypatch.setattr(mailer, "_send_via_smtp", lambda msg, cfg: False)
    monkeypatch.setattr(mailer, "_smtp_config", lambda: {
        "host": "smtp.down.invalid", "port": 25, "user": "", "password": "",
        "from_email": "x@y.z", "from_name": "T", "tls": False})
    with client.application.app_context():
        mid = services.create_contact_message(name="Ali", email="ali@example.org",
                                              topic="Autre question", body="Test SMTP down")
    c = auth_client(admin_user["id"])
    c.post(f"/admin/messages/{mid}/repondre", data={"reply": "Réponse conservée"})
    with client.application.app_context():
        m = services.get_contact_message(mid)
        assert m["status"] == "replied" and m["reply_sent"] == 0
    html = c.get("/admin/messages?status=replied").data.decode()
    assert "NON envoy" in html and "mailto:ali@example.org" in html


def test_dashboard_shows_inbox_counter_for_admin(client, auth_client, admin_user):
    import services
    with client.application.app_context():
        services.create_contact_message(name="Z", email="z@example.org", topic="t", body="corps du message")
    html = auth_client(admin_user["id"]).get("/espace").data.decode()
    assert "Messages reçus" in html and "à traiter" in html


def test_mailer_sync_send_reports_real_result(app_ctx, monkeypatch):
    import mailer
    monkeypatch.setattr(mailer, "_send_via_smtp", lambda msg, cfg: False)
    monkeypatch.setattr(mailer, "_smtp_config", lambda: {
        "host": "smtp.down.invalid", "port": 25, "user": "", "password": "",
        "from_email": "x@y.z", "from_name": "T", "tls": False})
    ok = mailer.send(to="a@b.co", subject="x", template="contact_ack",
                     context={"name": "A", "topic": "t", "body": "b", "reply_hours": 24},
                     async_=False)
    assert ok is False
