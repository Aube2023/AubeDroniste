"""Fiche des organisations (entreprise, école, boutique), 2026-10-05 : nom de
l'organisation saisi à part à l'inscription, fiche à part (bandeau, logo carré,
« Représentée par », coordonnées en boutons, rubriques de pilote masquées si
vides), rappel au propriétaire quand la fiche porte encore son nom personnel."""


def test_textes_org_dans_les_31_langues():
    import i18n
    keys = [k for k in i18n._T if k.startswith("org.")]
    assert len(keys) >= 14, "translations/_org.json n'a pas été chargé"
    trous = [(k, code) for k in keys for code in i18n.SUPPORTED if not (i18n._T[k].get(code) or "").strip()]
    assert trous == []
    for code in i18n.SUPPORTED:
        assert "Ana" in i18n.t("org.represented_by", code, name="Ana")


def test_inscription_entreprise_garde_le_nom_de_l_entreprise(client):
    import services
    r = client.post("/inscription", data={
        "username": "org_sky_x", "password": "demo1234", "confirm": "demo1234",
        "full_name": "Justin Exemple", "business_name": "SkyVision Drone",
        "role": "pilot", "kind": "company", "country": "Canada",
        "website": "https://skyvision.example"})
    assert r.status_code in (302, 303)
    with client.application.app_context():
        import db
        uid = db.fetchone("SELECT id FROM users WHERE username='org_sky_x'")["id"]
        prof = services.get_pilot_profile(uid)
    assert prof["business_name"] == "SkyVision Drone"
    assert prof["full_name"] == "Justin Exemple"
    assert prof["portfolio_url"] == "https://skyvision.example"
    reg = client.get("/inscription?role=pilot&kind=company").get_data(as_text=True)
    assert 'name="business_name"' in reg and 'id="reg-business"' in reg


def test_fiche_entreprise(client, make_user):
    import services
    u = make_user("org_fiche", role="pilot", country="Canada")
    with client.application.app_context():
        services.upsert_pilot_profile(u["id"], kind="company", business_name="Aéro Relevés",
                                      business_email="contact@aero.example",
                                      portfolio_url="https://www.aero.example/")
        full_name = services.get_pilot_profile(u["id"])["full_name"]
    html = client.get(f"/pilotes/{u['id']}").get_data(as_text=True)
    assert "org-hero kind-company" in html and 'class="org-banner"' in html
    assert "<h1>Aéro Relevés" in html
    assert f"Représentée par {full_name}" in html
    assert 'class="org-contacts"' in html and "mailto:contact@aero.example" in html
    assert ">aero.example<" in html
    # Rubriques de pilote vides : masquées pour une entreprise
    assert "Brevets &amp; qualifications" not in html and "Brevets & qualifications" not in html
    assert "Aéronefs déclarés" not in html
    assert "Services proposés" in html
    assert "recommander cette entreprise" in html


def test_rappel_au_proprietaire_si_la_fiche_porte_son_nom(client, auth_client, make_user):
    import services
    u = make_user("org_sans_nom", role="pilot", country="Canada")
    with client.application.app_context():
        full_name = services.get_pilot_profile(u["id"])["full_name"]
        services.upsert_pilot_profile(u["id"], kind="company", business_name=full_name)
    visiteur = client.get(f"/pilotes/{u['id']}").get_data(as_text=True)
    assert "org-nudge" not in visiteur and "Représentée par" not in visiteur
    proprio = auth_client(u["id"]).get(f"/pilotes/{u['id']}").get_data(as_text=True)
    assert "org-nudge" in proprio and "/espace/pilote#business_name" in proprio


def test_fiche_pilote_inchangee(client, make_user):
    """Un pilote garde le carnet de vol : pas de bandeau d'organisation."""
    import services
    u = make_user("org_pilote_pro", role="pilot", country="Canada")
    with client.application.app_context():
        services.upsert_pilot_profile(u["id"], kind="pro")
    html = client.get(f"/pilotes/{u['id']}").get_data(as_text=True)
    assert "org-hero" not in html and "org-banner" not in html
    assert "Brevets &amp; qualifications" in html or "Brevets & qualifications" in html
