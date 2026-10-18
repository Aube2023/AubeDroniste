"""Envoi d'un livrable vers AubeDrive / AubePhotos (2026-10-18).

Cas reel : le client clique « → AubePhotos » sur sa reservation, rien
n'arrive et aucun lien ne s'affiche. AubePilot appelait /api/internal/upload
(inexistant) avec un en-tete que ni AubeDrive ni AubePhotos ne lisent.
Contrat verifie ici : bon chemin, X-Internal-Secret, courriel AubeMail du
client, puis confirmation avec le lien du dossier ou de l'album.
"""
import os
from html import unescape

import pytest


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


@pytest.fixture()
def secret(monkeypatch):
    import aube_push
    monkeypatch.setattr(aube_push, "AUBE_INTERNAL_SECRET", "s3cret")
    monkeypatch.setattr(aube_push, "AUBEDRIVE_INTERNAL_URL", "http://127.0.0.1:5011")
    monkeypatch.setattr(aube_push, "AUBEPHOTOS_INTERNAL_URL", "http://127.0.0.1:5035")
    monkeypatch.setattr(aube_push, "AUBEDRIVE_URL", "https://drive.aubeetoilee.com")
    monkeypatch.setattr(aube_push, "AUBEPHOTOS_URL", "https://photos.aubeetoilee.com")


def _deliverable(booking_id, pilot_id, name, kind, mime):
    import services
    from config import UPLOAD_DIR
    stored = f"push_test_{booking_id}_{name}"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(UPLOAD_DIR, stored), "wb") as fh:
        fh.write(b"\xff\xd8 rushs")
    return services.add_deliverable(
        booking_id=booking_id, uploaded_by_user_id=pilot_id, label="",
        original_filename=name, stored_filename=stored, mime_type=mime,
        size_bytes=8, kind=kind)


@pytest.mark.parametrize("service,path,field,public", [
    ("aubedrive", "/api/files/internal/upload", ("folder_name", "AubePilot"),
     "https://drive.aubeetoilee.com/app?folder=7"),
    ("aubephotos", "/api/photos/internal/upload", ("album", "AubePilot"),
     "https://photos.aubeetoilee.com/albums/3"),
])
def test_envoi_donne_le_lien_et_la_confirmation(
        app_ctx, auth_client, funded_booking, client_user, pilot_user, secret,
        monkeypatch, service, path, field, public):
    import requests
    import services
    did = _deliverable(funded_booking, pilot_user["id"], "toit.jpg", "image", "image/jpeg")
    calls = []

    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "headers": headers,
                      "file": files["file"][0]})
        rel = "/app?folder=7" if service == "aubedrive" else "/albums/3"
        return _Resp(201, {"ok": True, "url": rel})

    monkeypatch.setattr(requests, "post", fake_post)
    c = auth_client(client_user["id"])
    r = c.post(f"/reservations/{funded_booking}/livrables/{did}/envoyer/{service}",
               follow_redirects=True)
    assert r.status_code == 200
    (call,) = calls
    assert call["url"].endswith(path) and call["url"].startswith("http://127.0.0.1")
    assert call["headers"] == {"X-Internal-Secret": "s3cret"}
    assert call["data"]["email"] == client_user["email"]
    assert call["data"][field[0]] == field[1]
    assert call["file"] == "toit.jpg"

    html = unescape(r.get_data(as_text=True))
    name = "AubeDrive" if service == "aubedrive" else "AubePhotos"
    assert f"« toit.jpg » est arrivé dans votre {name}." in html
    assert f'href="{public}"' in html
    d = services.get_deliverable(did)
    assert d[f"{service}_url"] == public and d[f"{service}_sent_at"]
    # Deja envoye : plus de bouton, un lien vers le dossier ou l'album
    assert f"/livrables/{did}/envoyer/{service}" not in html
    assert f"✓ {name} ↗" in html


@pytest.mark.parametrize("status,code,attendu", [
    (404, "no_account", "AubeDrive ne trouve aucun compte à l'adresse"),
    (413, "quota", "Votre espace AubeDrive est plein."),
    (403, "locked", "Votre compte AubeDrive est verrouillé"),
    (403, "unauthorized", "AubeDrive ne répond pas pour le moment."),
    (502, None, "AubeDrive ne répond pas pour le moment."),
])
def test_echec_message_clair(app_ctx, auth_client, funded_booking, client_user, pilot_user,
                             secret, monkeypatch, status, code, attendu):
    import requests
    import services
    did = _deliverable(funded_booking, pilot_user["id"], f"plan_{status}_{code}.pdf", "doc",
                       "application/pdf")
    body = {"error": "x", "code": code} if code else {}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(status, body))
    c = auth_client(client_user["id"])
    r = c.post(f"/reservations/{funded_booking}/livrables/{did}/envoyer/aubedrive",
               follow_redirects=True)
    assert attendu in unescape(r.get_data(as_text=True))
    assert services.get_deliverable(did)["aubedrive_sent_at"] is None


def test_aubephotos_refuse_un_pdf_sans_appel(app_ctx, auth_client, funded_booking, client_user,
                                             pilot_user, secret, monkeypatch):
    import requests
    did = _deliverable(funded_booking, pilot_user["id"], "rapport.pdf", "doc", "application/pdf")
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("aucun appel attendu"))
    c = auth_client(client_user["id"])
    r = c.post(f"/reservations/{funded_booking}/livrables/{did}/envoyer/aubephotos",
               follow_redirects=True)
    assert "AubePhotos n'accepte pas ce type de fichier." in unescape(r.get_data(as_text=True))


def test_seul_le_client_envoie(app_ctx, auth_client, funded_booking, pilot_user, secret):
    did = _deliverable(funded_booking, pilot_user["id"], "v.mp4", "video", "video/mp4")
    c = auth_client(pilot_user["id"])
    r = c.post(f"/reservations/{funded_booking}/livrables/{did}/envoyer/aubedrive")
    assert r.status_code == 403
