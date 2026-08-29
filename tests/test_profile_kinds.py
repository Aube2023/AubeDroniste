"""Types de profil (pro / récréatif / école) : inscription, profil, onglets de
l'annuaire, carte, API, fiche publique. Imports dans les tests (conftest)."""
import pytest


def test_create_user_with_kind_and_fallback(app_ctx, make_user):
    import auth
    import db
    import services
    u = make_user("kind_school", role="pilot")
    # make_user ne passe pas kind -> defaut pro
    assert services.get_pilot_profile(u["id"])["kind"] == "pro"
    uid = auth.create_user(username="kind_ecole_x", password="demo1234", full_name="École Test",
                           role="pilot", country="Canada", kind="school", send_welcome_email=False)
    assert db.fetchone("SELECT kind FROM pilot_profiles WHERE user_id=?", (uid,))["kind"] == "school"
    uid2 = auth.create_user(username="kind_bad_x", password="demo1234", full_name="Bad",
                            role="pilot", country="Canada", kind="boutique", send_welcome_email=False)
    assert db.fetchone("SELECT kind FROM pilot_profiles WHERE user_id=?", (uid2,))["kind"] == "pro"


def test_register_route_records_kind(client):
    import services
    r = client.post("/inscription", data={
        "username": "kind_reg_rec", "password": "demo1234", "confirm": "demo1234",
        "full_name": "Rec Pilote", "role": "pilot", "kind": "recreational", "country": "France"})
    assert r.status_code in (302, 303)
    with client.application.app_context():
        import db
        uid = db.fetchone("SELECT id FROM users WHERE username='kind_reg_rec'")["id"]
        assert services.get_pilot_profile(uid)["kind"] == "recreational"
    # ecole : le nom d'ecole est initialise avec le nom saisi
    client.get("/deconnexion")
    client.post("/inscription", data={
        "username": "kind_reg_school", "password": "demo1234", "confirm": "demo1234",
        "full_name": "Académie Drone Lyon", "role": "pilot", "kind": "school", "country": "France"})
    with client.application.app_context():
        import db
        uid = db.fetchone("SELECT id FROM users WHERE username='kind_reg_school'")["id"]
        prof = services.get_pilot_profile(uid)
        assert prof["kind"] == "school" and prof["business_name"] == "Académie Drone Lyon"
        assert services.public_name(prof) == "Académie Drone Lyon"   # en clair, pas masque


def test_search_filter_and_counts(app_ctx, make_user):
    import services
    pro = make_user("kind_s_pro", role="pilot", country="Belgique")
    rec = make_user("kind_s_rec", role="pilot", country="Belgique")
    sch = make_user("kind_s_sch", role="pilot", country="Belgique")
    services.upsert_pilot_profile(rec["id"], kind="recreational")
    services.upsert_pilot_profile(sch["id"], kind="school", business_name="Drone School BE",
                                  school_programs="A1/A3\nA2")
    ids = lambda kind: {p["id"] for p in services.search_pilots(country="Belgique", kind=kind, limit=500)}
    assert ids("") == {pro["id"], rec["id"], sch["id"]}
    assert ids("pro") == {pro["id"]}
    assert ids("recreational") == {rec["id"]}
    assert ids("school") == {sch["id"]}
    assert ids("boutique") == {pro["id"], rec["id"], sch["id"]}      # inconnu = tous
    counts = services.count_pilots_by_kind()
    assert counts["school"] >= 1 and counts["recreational"] >= 1 and counts["all"] >= counts["pro"]
    school = [p for p in services.search_pilots(country="Belgique", kind="school", limit=500)][0]
    assert school["business_name"] == "Drone School BE" and school["school_programs"].startswith("A1")


def test_directory_tabs_badges_and_school_name(client, make_user):
    import services
    with client.application.app_context():
        sch = make_user("kind_dir_sch", role="pilot", country="Suisse", full_name="Jean Secret")
        services.upsert_pilot_profile(sch["id"], kind="school", business_name="Swiss Drone Academy",
                                      school_programs="A1/A3\nSTS-01")
        rec = make_user("kind_dir_rec", role="pilot", country="Suisse")
        services.upsert_pilot_profile(rec["id"], kind="recreational")
    html = client.get("/pilotes?country=Suisse").data.decode()
    assert 'class="dir-tabs"' in html and "Pilotes pro" in html and "Écoles" in html and "Boutique" not in html
    assert "Swiss Drone Academy" in html and "Jean Secret" not in html     # ecole en clair, personne jamais
    assert 'kind-badge kind-school' in html and 'kind-badge kind-recreational' in html
    assert "A1/A3 · STS-01" in html
    html = client.get("/pilotes?country=Suisse&kind=school").data.decode()
    assert "Swiss Drone Academy" in html and 'kind-badge kind-recreational' not in html
    assert 'name="kind" value="school"' in html          # le filtre survit au formulaire
    html = client.get("/pilotes?country=Suisse&kind=pro").data.decode()
    assert "Swiss Drone Academy" not in html
    # onglet Specialites : chips
    assert 'id="specialites"' in html and "mission_type=thermographie" in html


def test_api_and_map_expose_kind(client, make_user):
    import services
    with client.application.app_context():
        sch = make_user("kind_api_sch", role="pilot", country="Autriche", lat=48.2, lng=16.4)
        services.upsert_pilot_profile(sch["id"], kind="school", business_name="Wien Drone Schule")
    j = client.get("/api/pilotes?country=Autriche&kind=school").get_json()
    assert j["count"] >= 1 and all(p["kind"] == "school" for p in j["pilots"])
    m = client.get("/api/map?country=Autriche&kind=school").get_json()
    assert m["pilots"] and all(p["kind"] == "school" for p in m["pilots"])
    assert any(p["name"] == "Wien Drone Schule" for p in m["pilots"])
    assert client.get("/api/map?country=Autriche&kind=pro").get_json()["pilots"] == []


def test_pilot_edit_sets_kind_and_programs(client, auth_client, make_user):
    import services
    u = make_user("kind_edit", role="pilot", country="France")
    c = auth_client(u["id"])
    html = c.get("/espace/pilote").data.decode()
    assert 'name="kind"' in html and 'name="school_programs"' in html
    r = c.post("/espace/pilote", data={"kind": "school", "business_name": "École du Ciel",
                                       "school_programs": "A2 (EASA)\nSTS-01", "country": "France",
                                       "currency": "EUR", "travel_radius_km": "50"})
    assert r.status_code in (200, 302, 303)
    with client.application.app_context():
        prof = services.get_pilot_profile(u["id"])
        assert prof["kind"] == "school" and "STS-01" in prof["school_programs"]
    # vue par un visiteur anonyme (le CTA « Contacter » est masque sur sa propre fiche)
    with client.application.test_client() as anon:
        page = anon.get(f"/pilotes/{u['id']}").data.decode()
    assert "École du Ciel" in page and "Formations proposées" in page and "Contacter cette école" in page


def test_become_pilot_with_kind(client, auth_client, make_user):
    import services
    u = make_user("kind_become", role="client")
    c = auth_client(u["id"])
    assert 'name="kind"' in c.get("/espace/pilote").data.decode()
    c.post("/espace/pilote", data={"become_pilot": "1", "kind": "recreational"})
    with client.application.app_context():
        prof = services.get_pilot_profile(u["id"])
        assert prof["role"] == "both" and prof["kind"] == "recreational"


def test_home_has_school_card_and_no_shop(client):
    html = client.get("/").data.decode()
    assert "role=pilot&amp;kind=school" in html or "role=pilot&kind=school" in html
    assert "Boutique" not in html
