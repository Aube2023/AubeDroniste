"""Accueil (bandeau sobre, recherche en une ligne), pied de page en colonnes
avec l'adresse de support, conditions d'utilisation alignées sur la
configuration, litige tranché en faveur du pilote."""


def test_accueil_bandeau_et_recherche(client):
    html = client.get("/").data.decode()
    assert 'class="hero hero-band hero-compact"' in html and 'class="hero-search"' in html
    assert 'name="near"' in html and 'name="country"' in html and 'name="mission_type"' in html
    assert "bg-aube" not in html and "EN VOL" not in html
    # Accueil direct (2026-09-20) : la carte pleine largeur puis les pilotes
    # avec leurs onglets arrivent AVANT les chiffres et les textes.
    i_map, i_pilots = html.index('id="carte"'), html.index('id="pilotes"')
    i_tabs, i_stats, i_world = html.index('class="dir-tabs"'), html.index('class="notam-strip"'), html.index('id="monde"')
    assert html.index('class="hero-search"') < i_map < i_pilots < i_tabs < i_stats < i_world
    assert 'class="map-bleed"' in html and 'id="aube-map"' in html


def test_pied_de_page_colonnes_et_courriel(client):
    html = client.get("/faq").data.decode()
    assert 'class="footer-grid"' in html
    assert 'href="mailto:support@aubemail.com" class="footer-mail"' in html
    assert "Le réseau" in html and "Entreprise" in html
    # Sans réseaux sociaux configurés, pas de colonne vide « Suivez-nous »
    assert "Suivez-nous" not in html or 'rel="me noopener"' in html


def test_cgu_reprennent_la_configuration(client):
    import config
    r = client.get("/cgu")
    assert r.status_code == 200
    html = r.data.decode()
    assert "Conditions générales d'utilisation de la plateforme AubePilot" in html
    assert f"{int(config.PLATFORM_FEE_PCT)} %" in html
    assert f"{config.AUTO_RELEASE_DAYS} jours" in html
    assert f"{int(config.CAMPAIGN_FEE_PCT)} % tout compris" in html
    assert f"{config.CANCELLATION_GRACE_HOURS} h après le paiement" in html
    assert "support@aubemail.com" in html and "Version 2.0" in html
    # Rien de promis que le code ne fait pas
    assert "abonnés Pro" not in html and "24 h." not in html


def test_litige_tranche_pour_le_pilote(app_ctx, funded_booking, make_user):
    import db
    import services
    b = services.get_booking(funded_booking)
    assert services.open_dispute(funded_booking, b["client_user_id"], "livrable flou")
    assert services.get_booking(funded_booking)["status"] == "disputed"
    admin = make_user("admin_litige", role="both")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    assert services.resolve_dispute_for_pilot(funded_booking, admin["id"]) is True
    after = services.get_booking(funded_booking)
    assert after["status"] == "completed" and after["stripe_transfer_id"]
    # Un second passage ne fait rien (plus en litige)
    assert services.resolve_dispute_for_pilot(funded_booking, admin["id"]) is False


def test_derniere_activite_par_tranche(app_ctx, make_user, auth_client, client):
    """Transparence sans horodatage : « actif aujourd'hui / cette semaine /
    ce mois-ci / il y a plus d'un mois », sur la fiche et sur la carte."""
    import db
    import services
    assert services.activity_bucket(None) is None
    assert services.activity_bucket("pas une date") is None
    u = make_user("actif_pilote", role="pilot", country="Canada", city="Laval")
    services.upsert_pilot_profile(u["id"], is_available=1, headline="Thermographie zz-actif-unique")
    db.execute("UPDATE users SET last_seen_at=datetime('now') WHERE id=?", (u["id"],))
    assert services.activity_bucket(db.fetchone("SELECT last_seen_at FROM users WHERE id=?", (u["id"],))["last_seen_at"]) == "today"
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "Actif aujourd" in html and "trust.active_title" not in html
    db.execute("UPDATE users SET last_seen_at=datetime('now', '-45 days') WHERE id=?", (u["id"],))
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "plus d'un mois" in html or "plus d&#39;un mois" in html
    db.execute("UPDATE users SET last_seen_at=datetime('now', '-3 days') WHERE id=?", (u["id"],))
    html = client.get("/pilotes?q=zz-actif-unique").data.decode()
    assert "Actif cette semaine" in html


def test_visionneuse_cachee_par_defaut():
    """Regression : le voile plein écran du portfolio s'affichait dès le
    chargement (display:flex prenait le pas sur l'attribut hidden)."""
    css = open("static/css/style.css", encoding="utf-8").read()
    assert ".lightbox[hidden] { display: none; }" in css
    js = open("static/js/app.js", encoding="utf-8").read()
    assert "box.hidden = true;" in js and "className = 'lightbox'" in js


# ---------------------------------------------------------------------------
# Partenaires : section preparee en coulisses, invisible tant qu'aucun
# partenaire n'est actif ; ouverte des le premier.
# ---------------------------------------------------------------------------

def _admin(make_user, auth_client):
    import db
    u = make_user("admin_part", role="client")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
    return auth_client(u["id"])


def test_partenaires_masques_puis_ouverts(app, make_user, auth_client, app_ctx):
    import db
    db.execute("DELETE FROM partners")
    client = app.test_client()      # visiteur anonyme (auth_client connecte le client partage)
    # Rien d'actif : page 404, pas de lien dans le pied de page, pas dans le sitemap.
    assert client.get("/partenaires").status_code == 404
    assert 'href="/partenaires"' not in client.get("/faq").data.decode()
    assert "/partenaires" not in client.get("/sitemap-fr.xml").data.decode()

    c = _admin(make_user, auth_client)
    # L'admin previsualise la page vide et voit l'avertissement.
    html = c.get("/partenaires").data.decode()
    assert "Aperçu réservé" in html   # (l'apostrophe est échappée par Jinja)
    # Ajout d'un partenaire NON actif : toujours masque pour le public.
    r = c.post("/admin/partenaires", data={"name": "AssurDrone", "kind": "insurance", "url": "assurdrone.example",
                                          "blurb": "Assurance RC pour pilotes.", "blurb_en": "Liability insurance for pilots."},
               follow_redirects=True)
    assert r.status_code == 200 and "Partenaire ajouté." in r.data.decode()
    p = db.fetchone("SELECT * FROM partners WHERE name=?", ("AssurDrone",))
    assert p["url"] == "https://assurdrone.example" and p["is_active"] == 0
    assert client.get("/partenaires").status_code == 404
    # Activation : la section s'ouvre partout.
    r = c.post(f"/admin/partenaires/{p['id']}", data={"name": "AssurDrone", "kind": "insurance",
                                                       "url": p["url"], "is_active": "1", "blurb": "Assurance RC pour pilotes.",
                                                       "blurb_en": "Liability insurance for pilots."},
               follow_redirects=True)
    assert "Partenaire mis à jour." in r.data.decode()
    html = client.get("/partenaires").data.decode()
    assert "AssurDrone" in html and "Assurance RC pour pilotes." in html and "Aperçu réservé" not in html
    assert 'href="https://assurdrone.example"' in html and 'rel="noopener sponsored"' in html
    assert "Liability insurance for pilots." in client.get("/en/partenaires").data.decode()
    client = app.test_client()      # la visite de /en/ a memorise l'anglais dans le cookie
    assert 'href="/partenaires"' in client.get("/faq").data.decode()
    assert "/partenaires" in client.get("/sitemap-fr.xml").data.decode()
    assert 'id="partenaires"' in client.get("/").data.decode()
    # Suppression : retour a l'etat masque.
    c.post(f"/admin/partenaires/{p['id']}/supprimer", follow_redirects=True)
    assert client.get("/partenaires").status_code == 404


def test_admin_partenaires_reserve_aux_admins(client, make_user, auth_client):
    u = make_user("pas_admin_part", role="pilot")
    assert auth_client(u["id"]).get("/admin/partenaires").status_code == 403
    assert client.get("/admin/partenaires").status_code in (302, 401, 403)
