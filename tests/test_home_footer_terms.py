"""Accueil (bandeau sobre, recherche en une ligne), pied de page en colonnes
avec l'adresse de support, conditions d'utilisation alignées sur la
configuration, litige tranché en faveur du pilote."""


def test_accueil_bandeau_et_recherche(client):
    html = client.get("/").data.decode()
    assert 'class="hero hero-band"' in html and 'class="hero-search"' in html
    assert 'name="near"' in html and 'name="country"' in html and 'name="mission_type"' in html
    assert "bg-aube" not in html and "EN VOL" not in html


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
