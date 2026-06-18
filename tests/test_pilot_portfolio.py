"""Portfolio pilote (/espace/pilote/portfolio).

Règle métier : les photos sont illimitées, les vidéos sont plafonnées à
config.MAX_PORTFOLIO_VIDEOS (hébergement/bande passante lourds). Avant, aucune
limite de nombre n'était appliquée — seulement la taille par fichier.
"""
from io import BytesIO

# IMPORTANT : pas d'import db/services/config/app au niveau module (cf conftest).


def _upload(c, filename, content=b"\x00" * 32):
    """POST multipart d'un fichier au portfolio. follow_redirects pour rendre
    la page (et donc les flashs) après le redirect PRG."""
    return c.post(
        "/espace/pilote/portfolio",
        data={"file": (BytesIO(content), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_portfolio_forbidden_for_client(make_user, auth_client):
    """Un client simple (non pilote) -> 403."""
    u = make_user("pfclient", role="client")
    c = auth_client(u["id"])
    assert c.get("/espace/pilote/portfolio").status_code == 403


def test_portfolio_photos_unlimited(make_user, auth_client, app_ctx):
    """Les photos n'ont AUCUNE limite de nombre (seulement la taille)."""
    import services

    u = make_user("pfphotos", role="pilot")
    c = auth_client(u["id"])
    for i in range(5):
        assert _upload(c, f"photo{i}.jpg").status_code == 200
    assert services.count_portfolio_items(u["id"], "image") == 5


def test_portfolio_videos_capped(make_user, auth_client, app_ctx):
    """Les vidéos sont plafonnées à MAX_PORTFOLIO_VIDEOS ; la suivante est
    refusée, mais les photos continuent de passer."""
    import config
    import services

    cap = config.MAX_PORTFOLIO_VIDEOS
    assert cap >= 1, "test calibré pour un plafond vidéo fini >= 1"

    u = make_user("pfvideos", role="pilot")
    c = auth_client(u["id"])

    # Les `cap` premières vidéos passent.
    for i in range(cap):
        assert _upload(c, f"clip{i}.mp4").status_code == 200
    assert services.count_portfolio_items(u["id"], "video") == cap

    # La (cap+1)-ème vidéo est refusée (redirige avec flash, compteur inchangé).
    r = _upload(c, "clip_over.mp4")
    assert r.status_code == 200
    assert "Maximum" in r.get_data(as_text=True)
    assert services.count_portfolio_items(u["id"], "video") == cap

    # ...mais une photo supplémentaire passe toujours (photos illimitées).
    assert _upload(c, "still.jpg").status_code == 200
    assert services.count_portfolio_items(u["id"], "image") == 1
