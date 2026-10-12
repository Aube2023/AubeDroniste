"""Galerie publique des réalisations (/realisations) et puces de spécialités
sur les cartes de l'annuaire, ajoutées le 2026-10-12 après comparaison avec
Droners.io (« Portfolio Reels », services et rayon sur chaque carte).

La galerie ne montre que ce que les fiches publiques montrent déjà : le
portfolio des pilotes inscrits, chaque pièce liée à la fiche de son pilote."""

# IMPORTANT : pas d'import db/services/config/app au niveau module (cf conftest).


def _piece(services, user_id, *, kind="image", title="Toiture à Laval"):
    ext = "mp4" if kind == "video" else "jpg"
    return services.add_portfolio_item(
        pilot_user_id=user_id, title=title, description="", kind=kind,
        original_filename=f"x.{ext}", stored_filename=f"portfolio_u{user_id}/1_x.{ext}",
        mime_type="video/mp4" if kind == "video" else "image/jpeg", size_bytes=10,
    )


def test_galerie_liste_les_pieces_avec_lien_vers_la_fiche(make_user, app_ctx, client):
    import services
    u = make_user("galerie_photo", role="pilot", full_name="Lea Galerie", city="Laval")
    _piece(services, u["id"], title="Façade du Vieux-Port")
    html = client.get("/realisations").get_data(as_text=True)
    assert "Façade du Vieux-Port" in html
    assert "Lea Galerie" in html and f'href="/pilotes/{u["id"]}"' in html
    assert 'data-full="/media/portfolio_u' in html          # visionneuse plein écran
    assert "noindex" not in html                            # indexable une fois remplie


def test_onglets_photo_et_video(make_user, app_ctx, client):
    import services
    u = make_user("galerie_onglets", role="pilot")
    _piece(services, u["id"], kind="image", title="Photo-onglet-unique")
    _piece(services, u["id"], kind="video", title="Video-onglet-unique")
    videos = client.get("/realisations?type=video").get_data(as_text=True)
    assert "Video-onglet-unique" in videos and "Photo-onglet-unique" not in videos
    assert "noindex, follow" in videos                      # variante par paramètre
    photos = client.get("/realisations?type=image").get_data(as_text=True)
    assert "Photo-onglet-unique" in photos and "Video-onglet-unique" not in photos
    counts = services.public_portfolio_counts()
    assert counts["all"] == counts["image"] + counts["video"] and counts["video"] >= 1


def test_compte_supprime_ou_client_absent_de_la_galerie(make_user, app_ctx, client):
    import db
    import services
    parti = make_user("galerie_parti", role="pilot")
    _piece(services, parti["id"], title="Piece-de-compte-supprime")
    db.execute("UPDATE users SET deleted_at=datetime('now') WHERE id=?", (parti["id"],))
    ancien = make_user("galerie_ex_pilote", role="pilot")
    _piece(services, ancien["id"], title="Piece-d-un-ex-pilote")
    db.execute("UPDATE users SET role='client' WHERE id=?", (ancien["id"],))
    html = client.get("/realisations").get_data(as_text=True)
    assert "Piece-de-compte-supprime" not in html
    assert "Piece-d-un-ex-pilote" not in html


def test_galerie_traduite_et_au_plan_du_site(make_user, app_ctx, client):
    import services
    u = make_user("galerie_en", role="pilot")
    _piece(services, u["id"])
    # Lien dans le pied de page de toutes les pages (avant de choisir l'anglais :
    # ce choix redirigerait ensuite les URL nues vers /en/).
    assert 'href="/realisations"' in client.get("/faq").get_data(as_text=True)
    assert "/realisations" in client.get("/sitemap-fr.xml").get_data(as_text=True)
    en = client.get("/en/realisations").get_data(as_text=True)
    assert "Work by our pilots" in en


def test_accueil_montre_les_dernieres_photos(make_user, app_ctx, client):
    import services
    u = make_user("galerie_accueil", role="pilot")
    _piece(services, u["id"], title="Photo-pour-accueil")
    html = client.get("/").get_data(as_text=True)
    assert 'id="realisations"' in html and "Photo-pour-accueil" in html


def test_carte_annuaire_specialites_et_rayon(make_user, app_ctx, client):
    import db
    import services
    u = make_user("carte_specs", role="pilot", full_name="Sam Puces")
    services.set_pilot_specialties(u["id"], ["toiture", "photo", "mapping", "btp", "3d"])
    db.execute("UPDATE pilot_profiles SET travel_radius_km=120, is_available=1 WHERE user_id=?", (u["id"],))
    found = [p for p in services.search_pilots(text="Sam Puces", limit=5) if p["id"] == u["id"]]
    assert found and found[0]["specialties"] == ["photo", "mapping", "3d", "toiture", "btp"]  # ordre du catalogue
    html = client.get("/pilotes?q=Sam+Puces").get_data(as_text=True)
    assert 'class="spec-chip"' in html and "Photographie aérienne" in html
    assert ">+2<" in html.replace(" ", "")                  # 5 spécialités : 3 puces + « +2 »
    assert "Rayon 120 km" in html
