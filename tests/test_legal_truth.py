"""Ce que le site affiche doit être vrai (audit du 2026-10-12) : brevet expiré
qui ne compte plus comme vérifié, coordonnées du pilote absentes du devis PDF
avant le paiement, mentions légales à jour, crédits de licence exacts, page
Part 107 qui ne prête pas la vérification à la FAA."""


def test_brevet_verifie_mais_expire_ne_compte_plus(app_ctx, make_user):
    import db
    import services
    valide = make_user("cert_valide", role="pilot", country="États-Unis", city="Austin")
    expire = make_user("cert_expire", role="pilot", country="États-Unis", city="Austin")
    for p, fin in ((valide, "2099-01-01"), (expire, "2020-01-01")):
        services.upsert_pilot_profile(p["id"], is_available=1)
        services.add_certification(p["id"], authority="FAA", title="Remote Pilot Certificate",
                                   reference="1234567", issued_at="2019-01-01", expires_at=fin)
        db.execute("UPDATE pilot_certifications SET is_verified=1, review_status='verified' "
                   "WHERE pilot_user_id=?", (p["id"],))
        services.refresh_user_verified(p["id"])
    ids = {r["id"] for r in services.search_pilots(only_verified=True, limit=500)}
    assert valide["id"] in ids and expire["id"] not in ids
    tous = {r["id"]: r for r in services.search_pilots(limit=500)}
    assert tous[valide["id"]]["verified_authorities"] == ["FAA"]
    assert tous[expire["id"]]["verified_authorities"] == []


def test_devis_pdf_sans_coordonnees_avant_paiement(auth_client, app_ctx, client_user, open_mission,
                                                   pending_bid, monkeypatch):
    import pdfgen
    import services
    vus = []
    monkeypatch.setattr(pdfgen, "devis_pdf", lambda booking, label="": vus.append(booking) or b"%PDF")
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    c = auth_client(client_user["id"])
    assert c.get(f"/reservations/{booking_id}/devis.pdf").status_code == 200
    assert vus[-1]["pilot_email"] is None and vus[-1]["pilot_phone"] is None
    assert vus[-1]["pilot_portfolio_url"] is None
    services.mark_booking_funded(booking_id, payment_intent_id="pi_fake_devis")
    assert c.get(f"/reservations/{booking_id}/devis.pdf").status_code == 200
    assert vus[-1]["pilot_email"]


def test_mentions_legales_a_jour(client):
    html = client.get("/mentions-legales").get_data(as_text=True)
    assert "524/2013" not in html and "Stripe Payments Europe" not in html
    assert "en cours d&#39;immatriculation" not in html and "en cours d'immatriculation" not in html
    assert "affilié à aucune autorité" in html
    assert "GeoNames" in html and "CC BY 4.0" in html and "ODbL 1.0" in html


def test_credit_adsb_dans_toutes_les_langues():
    import i18n
    for code in i18n.SUPPORTED:
        credit = i18n.t("adsb.credit", code)
        assert "ODbL" in credit and "CC0" not in credit, code


def test_page_part107_ne_prete_pas_la_verification_a_la_faa(client):
    html = client.get("/en/part-107").get_data(as_text=True)
    assert "FAA-verified" not in html
    assert "the day your certificate is checked" not in html
    assert "checked against the FAA registry" in html


def test_fenetre_de_grace_remboursement_integral_meme_a_moins_de_24_h():
    """CGU art. 14.1 : jusqu'à 2 h après le paiement, remboursement intégral,
    même si la mission a lieu dans moins de 24 h."""
    from datetime import datetime, timedelta, timezone
    import services
    now = datetime.now(timezone.utc)
    res = services.compute_cancellation_fee({
        "agreed_price": 1000,
        "scheduled_at": (now + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
        "paid_at": (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
    })
    assert res["within_grace"] and not res["is_late"]
    assert res["fee_amount"] == 0.0 and res["service_fee_amount"] == 0.0
    assert res["refund_amount"] == 1000.0


def test_pas_de_versement_manuel_promis():
    """Aucun outil de versement manuel n'existe : le site ne doit pas le promettre."""
    import content
    import i18n
    for code in i18n.SUPPORTED:
        texte = i18n.t("nav.payouts_country", code)
        assert "AubePilot" in texte, code
    faq = " ".join(q["answer"] for lang in ("fr", "en") for q in content.faq(lang))
    assert "manuellement par notre équipe" not in faq and "pays out manually" not in faq
    assert "Maghreb" not in faq


def test_inscription_exige_l_acceptation_des_conditions(client, app_ctx):
    """CGU art. 3 : sans la case cochée, aucun compte n'est créé ; avec,
    la version acceptée et la date sont enregistrées."""
    import config
    import db
    html = client.get("/inscription").get_data(as_text=True)
    assert 'name="accept_terms"' in html and 'href="/cgu"' in html
    form = {"username": "cgu_refus", "password": "demo1234", "confirm": "demo1234",
            "full_name": "Sans Case", "role": "client", "country": "Canada"}
    r = client.post("/inscription", data=form)
    assert "Cochez la case" in r.get_data(as_text=True)
    assert db.fetchone("SELECT id FROM users WHERE username='cgu_refus'") is None
    client.post("/inscription", data={**form, "username": "cgu_ok", "accept_terms": "1"})
    row = db.fetchone("SELECT terms_version, terms_accepted_at FROM users WHERE username='cgu_ok'")
    assert row and row["terms_version"] == config.TERMS_VERSION and row["terms_accepted_at"]
