"""Miniatures WebP de /media (thumbs.py) et en-têtes de cache.

Audit du 2026-10-07 : quatre photos de 0,6 à 1,4 Mo sur l'accueil, affichées
en rond de 56 px ou en bandeau de 120 px. Les pages servent maintenant une
copie WebP réduite ; ces tests verrouillent la taille, le format, les gardes
de confidentialité de /media et l'effacement avec le compte.
"""
import os
from io import BytesIO

import pytest
from PIL import Image

import thumbs
from config import UPLOAD_DIR


def _photo(name: str, size=(2400, 1600), mode="RGB") -> str:
    os.makedirs(os.path.dirname(os.path.join(UPLOAD_DIR, name)), exist_ok=True)
    path = os.path.join(UPLOAD_DIR, name)
    Image.new(mode, size, (200, 120, 40) if mode == "RGB" else (200, 120, 40, 128)).save(path)
    return path


def test_miniature_reduite_en_webp(client):
    _photo("cover_u1_900001.jpg")
    r = client.get("/media/w640/cover_u1_900001.jpg.webp")
    assert r.status_code == 200
    assert r.mimetype == "image/webp"
    assert "immutable" in r.headers["Cache-Control"] and "max-age=31536000" in r.headers["Cache-Control"]
    im = Image.open(BytesIO(r.data))
    assert im.format == "WEBP" and im.size == (640, 427)


def test_petite_image_jamais_agrandie_et_transparence_gardee(client):
    _photo("avatar_u1_900002.png", size=(100, 100), mode="RGBA")
    r = client.get("/media/w160/avatar_u1_900002.png.webp")
    assert r.status_code == 200
    im = Image.open(BytesIO(r.data))
    assert im.size == (100, 100) and im.mode == "RGBA"


def test_original_garde_un_cache_long(client):
    _photo("avatar_u1_900003.jpg", size=(50, 50))
    r = client.get("/media/avatar_u1_900003.jpg")
    assert r.status_code == 200 and "max-age=31536000" in r.headers["Cache-Control"]


@pytest.mark.parametrize("url", [
    "/media/w641/cover_u1_900004.jpg.webp",     # largeur hors liste : pas de taille arbitraire
    "/media/w640/cover_u1_900004.jpg",          # sans .webp
    "/media/w640/u1_cert_900004.jpg.webp",      # fichier prive : meme liste blanche que /media
    "/media/w640/cover_u1_absent.jpg.webp",     # original absent (photo retiree)
    "/media/w640/../cover_u1_900004.jpg.webp",
])
def test_miniature_refusee(client, url):
    _photo("cover_u1_900004.jpg", size=(50, 50))
    _photo("u1_cert_900004.jpg", size=(50, 50))
    assert client.get(url).status_code == 404


def test_fichier_illisible_renvoie_a_l_original(client):
    with open(os.path.join(UPLOAD_DIR, "avatar_u1_900005.jpg"), "wb") as f:
        f.write(b"pas une image")
    r = client.get("/media/w160/avatar_u1_900005.jpg.webp")
    assert r.status_code == 302 and r.headers["Location"].endswith("/media/avatar_u1_900005.jpg")


def test_miniature_refaite_quand_l_original_change(client):
    path = _photo("cover_u1_900006.jpg", size=(800, 400))
    assert Image.open(BytesIO(client.get("/media/w640/cover_u1_900006.jpg.webp").data)).size == (640, 320)
    _photo("cover_u1_900006.jpg", size=(800, 800))
    later = os.path.getmtime(path) + 5
    os.utime(path, (later, later))
    assert Image.open(BytesIO(client.get("/media/w640/cover_u1_900006.jpg.webp").data)).size == (640, 640)


def test_url_des_gabarits():
    assert thumbs.url("uploads/avatar_u1_1.png", 160) == "/media/w160/avatar_u1_1.png.webp"
    assert thumbs.url("portfolio_u1/2_vue.jpg", 1280) == "/media/w1280/portfolio_u1/2_vue.jpg.webp"
    # Video, GIF ou largeur inconnue : l'original.
    assert thumbs.url("portfolio_u1/3_clip.mp4", 1280) == "/media/portfolio_u1/3_clip.mp4"
    assert thumbs.url("uploads/avatar_u1_1.gif", 160) == "/media/avatar_u1_1.gif"
    assert thumbs.url("uploads/avatar_u1_1.png", 999) == "/media/avatar_u1_1.png"


def test_suppression_du_compte_efface_les_miniatures(make_user, client, app_ctx):
    import db
    import services
    u = make_user("thumbs_purge", role="pilot")
    uid = u["id"]
    _photo(f"avatar_u{uid}_1.jpg", size=(400, 400))
    _photo(f"portfolio_u{uid}/1_vue.jpg", size=(400, 400))
    db.execute("UPDATE users SET avatar_path=? WHERE id=?", (f"uploads/avatar_u{uid}_1.jpg", uid))
    assert client.get(f"/media/w160/avatar_u{uid}_1.jpg.webp").status_code == 200
    assert client.get(f"/media/w1280/portfolio_u{uid}/1_vue.jpg.webp").status_code == 200
    assert os.path.exists(thumbs.path_for(f"avatar_u{uid}_1.jpg", 160))
    assert services.delete_account(uid)["ok"]
    assert not os.path.exists(thumbs.path_for(f"avatar_u{uid}_1.jpg", 160))
    assert not os.path.exists(thumbs.path_for(f"portfolio_u{uid}/1_vue.jpg", 1280))


def test_accueil_et_annuaire_servent_les_miniatures_avec_dimensions(make_user, client, app_ctx):
    import db
    # Pays peu peuple : la base de test est partagee, l'annuaire est pagine.
    u = make_user("thumbs_card", role="pilot", country="Mongolie", city="Oulan-Bator")
    db.execute("UPDATE pilot_profiles SET is_available=1 WHERE user_id=?", (u["id"],))
    db.execute("UPDATE users SET avatar_path=? WHERE id=?", (f"uploads/avatar_u{u['id']}_7.jpg", u["id"]))
    html = client.get("/pilotes?country=Mongolie").data.decode()
    card = html.split(f'data-pilot-id="{u["id"]}"')[1].split("</a>")[0]
    assert f'src="/media/w160/avatar_u{u["id"]}_7.jpg.webp"' in card
    assert 'width="56" height="56"' in card
