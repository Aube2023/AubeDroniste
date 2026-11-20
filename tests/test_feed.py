"""Fil des pilotes (/fil, 2026-11-20)."""
import io
from html import unescape

import pytest


def _pilot(make_user, name="fp", country="Canada"):
    return make_user(name, role="pilot", country=country, city="Montréal")


def test_lecture_publique_et_vide(client):
    html = unescape(client.get("/fil").data.decode())
    assert "Fil des pilotes" in html
    assert "Aucune publication pour l'instant" in html
    assert 'name="body"' not in html  # pas de formulaire pour un visiteur


def test_pilote_publie_texte_et_photo(auth_client, make_user, tmp_path, monkeypatch):
    import app as appmod, feed
    monkeypatch.setattr(appmod, "UPLOAD_DIR", str(tmp_path))
    p = _pilot(make_user)
    c = auth_client(p["id"])
    r = c.post("/fil", data={"body": "Premier vol au-dessus du fleuve https://exemple.com"})
    assert r.status_code == 302
    r = c.post("/fil", data={"body": "", "media": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "vue.png")},
               content_type="multipart/form-data")
    assert r.status_code == 302
    posts = feed.list_posts(None, author_id=p["id"])
    assert len(posts) == 2
    assert posts[0]["media_kind"] == "image" and posts[0]["media_path"].startswith(f"feed_u{p['id']}/")
    html = unescape(c.get("/fil").data.decode())
    assert "Premier vol au-dessus du fleuve" in html
    assert 'href="https://exemple.com"' in html and "nofollow" in html
    # la photo est servie publiquement
    assert c.get("/media/" + posts[0]["media_path"]).status_code == 200


def test_publication_vide_ou_mauvais_format(auth_client, make_user, tmp_path, monkeypatch):
    import app as appmod, feed
    monkeypatch.setattr(appmod, "UPLOAD_DIR", str(tmp_path))
    me = _pilot(make_user)
    c = auth_client(me["id"])
    r = c.post("/fil", data={"body": "   "}, follow_redirects=True)
    assert "Écrivez un texte" in unescape(r.data.decode())
    r = c.post("/fil", data={"body": "x", "media": (io.BytesIO(b"MZ"), "virus.exe")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "Format non accepté" in unescape(r.data.decode())
    assert feed.list_posts(None, author_id=me["id"]) == []


def test_client_ne_publie_pas_mais_commente_et_aime(auth_client, make_user):
    import feed
    p = _pilot(make_user)
    pid = feed.create_post(p["id"], "Inspection de toiture terminée")
    cl = make_user("fc", role="client", country="Canada", city="Laval")
    c = auth_client(cl["id"])
    assert c.post("/fil", data={"body": "je tente"}).status_code == 403
    html = unescape(c.get("/fil").data.decode())
    assert "Seuls les pilotes peuvent publier" in html
    r = c.post(f"/fil/{pid}/aimer", headers={"X-Requested-With": "fetch"})
    assert r.get_json() == {"liked": True, "likes": 1}
    r = c.post(f"/fil/{pid}/aimer", headers={"X-Requested-With": "fetch"})
    assert r.get_json() == {"liked": False, "likes": 0}
    c.post(f"/fil/{pid}/commenter", data={"body": "Superbe travail !"})
    post = feed.get_post(pid)
    assert [x["body"] for x in post["comments"]] == ["Superbe travail !"]
    # client masque (Prenom N.), pilote sous son nom complet
    assert post["comments"][0]["author_is_pilot"] is False


def test_suppression_reservee_auteur_ou_admin(auth_client, make_user):
    import db, feed
    p = _pilot(make_user)
    other = _pilot(make_user, "fo")
    pid = feed.create_post(p["id"], "À supprimer")
    assert auth_client(other["id"]).post(f"/fil/{pid}/supprimer").status_code == 403
    cid = feed.add_comment(pid, other["id"], "commentaire")
    # l'auteur de la publication peut retirer un commentaire sous sa publication
    assert auth_client(p["id"]).post(f"/fil/commentaire/{cid}/supprimer").status_code == 302
    assert feed.get_post(pid)["comments"] == []
    assert auth_client(p["id"]).post(f"/fil/{pid}/supprimer").status_code == 302
    assert feed.get_post(pid) is None
    assert auth_client(p["id"]).get(f"/fil/{pid}").status_code == 404


def test_blocage_masque_et_interdit(auth_client, make_user):
    import feed, services
    p = _pilot(make_user)
    other = _pilot(make_user, "fb")
    pid = feed.create_post(p["id"], "Visible sauf pour qui est bloqué")
    services.block_user(p["id"], other["id"])
    assert feed.list_posts(other["id"], author_id=p["id"]) == []
    with pytest.raises(PermissionError):
        feed.add_comment(pid, other["id"], "coucou")
    assert len(feed.list_posts(None, author_id=p["id"])) == 1


def test_signalement_publication(auth_client, make_user):
    import db, feed
    p = _pilot(make_user)
    other = _pilot(make_user, "fr")
    pid = feed.create_post(p["id"], "Contenu douteux")
    r = auth_client(other["id"]).post("/signaler", data={"target_type": "post", "target_id": pid,
                                                         "reason": "spam", "next": "/fil"})
    assert r.status_code == 302
    row = db.fetchone("SELECT target_user_id FROM reports WHERE target_type='post' AND target_id=?", (pid,))
    assert row and row["target_user_id"] == p["id"]


def test_lien_dans_le_menu_et_version_anglaise(client):
    assert 'href="/fil"' in client.get("/").data.decode()
    html = unescape(client.get("/en/fil").data.decode())
    assert "Pilot feed" in html
