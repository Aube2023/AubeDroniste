"""Image de couverture : bandeau paysage en haut de la fiche publique, à côté
de la photo de profil. Téléversée depuis l'espace pilote, servie par /media,
remplacée puis retirée du disque, prioritaire pour l'aperçu partagé."""
import os
from io import BytesIO

from config import UPLOAD_DIR

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _upload(c, name="vue.png", data=PNG):
    return c.post("/espace/pilote/couverture", data={"cover": (BytesIO(data), name)},
                  content_type="multipart/form-data", follow_redirects=True)


def test_couverture_televersee_affichee_remplacee_retiree(make_user, auth_client, app_ctx, client):
    import db
    u = make_user("cover_pilote", role="pilot")
    c = auth_client(u["id"])

    # Sans couverture : ni bandeau ni classe has-cover, formulaire présent.
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "flightbook-cover" not in html and "has-cover" not in html
    edit = c.get("/espace/pilote").data.decode()
    assert 'action="/espace/pilote/couverture"' in edit and "cover-frame placeholder" in edit

    r = _upload(c)
    assert r.status_code == 200 and "Image de couverture mise à jour." in r.data.decode()
    row = db.fetchone("SELECT cover_path, avatar_path FROM users WHERE id=?", (u["id"],))
    assert row["cover_path"].startswith(f"uploads/cover_u{u['id']}_") and row["cover_path"].endswith(".png")
    assert row["avatar_path"] is None                      # la photo de profil n'est pas touchée
    first = row["cover_path"]
    assert os.path.exists(os.path.join(UPLOAD_DIR, first[8:]))

    # Fiche publique : bandeau + photo qui chevauche ; /media sert le fichier.
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert 'class="flightbook-hero has-cover"' in html
    assert '<div class="flightbook-cover">' in html and "/media/" + first[8:] in html
    assert client.get("/media/" + first[8:]).status_code == 200
    # og:image = la couverture (paysage) avant tout.
    assert 'property="og:image" content="' in html and first[8:] in html.split('property="og:image"')[1][:200]

    # Remplacement : l'ancienne part du disque.
    r = _upload(c, name="autre.jpg")
    assert r.status_code == 200
    second = db.fetchone("SELECT cover_path FROM users WHERE id=?", (u["id"],))["cover_path"]
    assert second != first and second.endswith(".jpg")
    assert not os.path.exists(os.path.join(UPLOAD_DIR, first[8:]))
    assert os.path.exists(os.path.join(UPLOAD_DIR, second[8:]))

    # Retrait.
    r = c.post("/espace/pilote/couverture/supprimer", follow_redirects=True)
    assert "Image de couverture retirée." in r.data.decode()
    assert db.fetchone("SELECT cover_path FROM users WHERE id=?", (u["id"],))["cover_path"] is None
    assert not os.path.exists(os.path.join(UPLOAD_DIR, second[8:]))
    assert "flightbook-cover" not in client.get(f"/pilotes/{u['id']}").data.decode()


def test_couverture_refuse_pdf_et_fichier_manquant(make_user, auth_client, app_ctx):
    import db
    u = make_user("cover_refus", role="pilot")
    c = auth_client(u["id"])
    r = _upload(c, name="doc.pdf", data=b"%PDF-1.4")
    assert "Format non accepté" in r.data.decode()
    r = c.post("/espace/pilote/couverture", data={}, content_type="multipart/form-data", follow_redirects=True)
    assert "Aucun fichier sélectionné." in r.data.decode()
    assert db.fetchone("SELECT cover_path FROM users WHERE id=?", (u["id"],))["cover_path"] is None


def test_couverture_exige_connexion(client):
    r = client.post("/espace/pilote/couverture", data={"cover": (BytesIO(PNG), "x.png")},
                    content_type="multipart/form-data")
    assert r.status_code in (302, 401, 403)


def test_media_sert_la_couverture_mais_pas_un_faux_prefixe(client):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(UPLOAD_DIR, "cover_u1_123.png"), "wb") as f:
        f.write(PNG)
    assert client.get("/media/cover_u1_123.png").status_code == 200
    # `cover_` sans identifiant d'utilisateur reste hors liste blanche.
    with open(os.path.join(UPLOAD_DIR, "cover_secret.png"), "wb") as f:
        f.write(PNG)
    assert client.get("/media/cover_secret.png").status_code == 404


def test_suppression_du_compte_efface_la_couverture(make_user, auth_client, app_ctx):
    import db
    import services
    u = make_user("cover_purge", role="pilot")
    _upload(auth_client(u["id"]))
    rel = db.fetchone("SELECT cover_path FROM users WHERE id=?", (u["id"],))["cover_path"]
    path = os.path.join(UPLOAD_DIR, rel[8:])
    assert os.path.exists(path)
    assert services.delete_account(u["id"])["ok"]
    assert db.fetchone("SELECT cover_path FROM users WHERE id=?", (u["id"],))["cover_path"] is None
    assert not os.path.exists(path)
