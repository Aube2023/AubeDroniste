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
