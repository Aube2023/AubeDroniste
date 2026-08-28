"""Découverte : recherche par code postal (géocodage + cache), filtres
« confiance » (brevet vérifié / assuré / autorité), FAQ, contact, sitemap.

Conventions conftest : jamais d'import app/db/services au niveau module.
Le réseau est TOUJOURS mocké (geocode._fetch) : aucun appel Nominatim en test.
"""
import os
import time

import pytest


# ---------------------------------------------------------------------------
# Géocodage
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_geocoder(monkeypatch, app_ctx):
    """Remplace l'appel réseau par une table locale + compteur d'appels."""
    import geocode
    table = {
        "75011": {"lat": 48.8594, "lng": 2.3765, "label": "75011 Paris, France", "country": "France"},
        "g5y":   {"lat": 46.1200, "lng": -70.6700, "label": "G5Y Saint-Georges, Canada", "country": "Canada"},
        "h2x 1y4": {"lat": 45.5120, "lng": -73.5680, "label": "H2X 1Y4 Montréal, Canada", "country": "Canada"},
    }
    calls = []

    def _fetch(query, country="", _lang="fr"):
        calls.append((query, country))
        if country == "Zzland":          # indice pays incoherent -> rien
            return None
        return table.get(geocode.normalize(query))

    monkeypatch.setattr(geocode, "_fetch", _fetch)
    return calls


def test_geocode_lookup_uses_cache(fake_geocoder):
    import geocode
    # premier appel -> reseau (mock) ; deuxieme -> cache DB, pas d'appel
    r1 = geocode.lookup("75011 cache-test-a")
    assert r1 is None  # inconnu de la table : negatif memorise
    n = len(fake_geocoder)
    assert geocode.lookup("75011 cache-test-a") is None
    assert len(fake_geocoder) == n, "le resultat negatif doit etre servi depuis le cache"

    r = geocode.lookup("  75011 ")
    assert r and abs(r["lat"] - 48.8594) < 1e-6 and r["label"].startswith("75011")
    n = len(fake_geocoder)
    r2 = geocode.lookup("75011")
    assert r2 == r
    assert len(fake_geocoder) == n, "resultat positif servi depuis le cache"


def test_geocode_lookup_short_query_is_none(fake_geocoder):
    import geocode
    assert geocode.lookup("") is None
    assert geocode.lookup("a") is None
    assert fake_geocoder == []


def test_geocode_fallback_without_country(fake_geocoder):
    """Indice pays incoherent -> on retente sans le pays."""
    import geocode
    r = geocode.lookup("75011", country="Zzland")
    assert r and r["country"] == "France"
    assert [c for _query, c in fake_geocoder] == ["Zzland", ""], "essai avec l'indice, puis sans"


def test_canada_fsa_offline_lookup(fake_geocoder):
    """RTA canadienne seule : resolue SANS reseau via geodata/ca_fsa.csv."""
    import geocode
    r = geocode.lookup("g5y")
    assert r and r["country"] == "Canada" and "Saint-Georges" in r["label"]
    assert abs(r["lat"] - 46.13) < 0.05 and abs(r["lng"] + 70.64) < 0.05
    assert fake_geocoder == [], "aucun appel reseau pour une RTA"
    assert geocode.canada_fsa("M5V") and "Toronto" in geocode.canada_fsa("M5V")["label"]
    assert geocode.canada_fsa("75011") is None
    assert geocode.canada_fsa("ZZZ") is None
    # code complet inconnu du geocodeur en ligne -> repli sur la RTA
    r = geocode.lookup("H2X 9Z9")
    assert r and "Plateau" in r["label"] and r.get("approx")
    assert fake_geocoder, "le code complet a d'abord ete tente en ligne"


def test_nominatim_result_sanity_check():
    """Un code postal saisi ne doit jamais renvoyer un lieu au code different."""
    import geocode
    ok = {"type": "postcode", "address": {"postcode": "75011"}}
    bad = {"type": "hamlet", "address": {"postcode": "41519"}}
    assert geocode._result_matches_postal_query("75011", ok)
    assert not geocode._result_matches_postal_query("H2X 0A1", bad)
    assert geocode._result_matches_postal_query("H2X 1Y4", {"address": {"postcode": "H2X 1Y4"}})
    assert geocode._result_matches_postal_query("Casablanca", bad)  # texte libre : pas filtre


def test_looks_like_postal_code():
    import geocode
    assert geocode.looks_like_postal_code("75011")
    assert geocode.looks_like_postal_code("H2X 1Y4")
    assert geocode.looks_like_postal_code("G5Y")
    assert not geocode.looks_like_postal_code("Casablanca")
    assert not geocode.looks_like_postal_code("12 rue de la Paix")


def test_api_geocode_requires_q(client):
    assert client.get("/api/geocode").status_code == 400
    assert client.get("/api/geocode?q=a").status_code == 400


def test_api_geocode_found_and_not_found(client, fake_geocoder):
    r = client.get("/api/geocode?q=H2X%201Y4")
    assert r.status_code == 200
    j = r.get_json()
    assert j["found"] is True and abs(j["lat"] - 45.512) < 1e-3 and "Montréal" in j["label"]
    r = client.get("/api/geocode?q=nulle-part-xyz")
    assert r.status_code == 200 and r.get_json()["found"] is False


# ---------------------------------------------------------------------------
# Recherche « autour de » : tri par distance, rayon strict, carte centree
# ---------------------------------------------------------------------------

def test_pilots_search_near_sorts_by_distance(client, make_user, fake_geocoder):
    far = make_user("near_far", role="pilot", country="France", city="Paris",
                    lat=48.8566, lng=2.3522)
    close = make_user("near_close", role="pilot", country="Canada",
                      city="Montréal", lat=45.50, lng=-73.57)
    r = client.get("/api/pilotes?near=H2X%201Y4&only_available=1")
    assert r.status_code == 200
    j = r.get_json()
    assert j["near"]["found"] is True and j["near"]["label"].startswith("H2X 1Y4")
    ids = [p["id"] for p in j["pilots"]]
    assert close["id"] in ids and far["id"] in ids
    assert ids.index(close["id"]) < ids.index(far["id"])
    dist = {p["id"]: p["distance_km"] for p in j["pilots"]}
    assert dist[close["id"]] < 20 and dist[far["id"]] > 5000

    # rayon strict : le pilote parisien disparait
    r = client.get("/api/pilotes?near=H2X%201Y4&near_only=1&radius_km=100")
    ids = [p["id"] for p in r.get_json()["pilots"]]
    assert close["id"] in ids and far["id"] not in ids


def test_pilots_page_shows_near_label_and_distance(client, make_user, fake_geocoder):
    make_user("near_page", role="pilot", country="Canada", city="Montréal",
              lat=45.50, lng=-73.57)
    r = client.get("/pilotes?near=H2X+1Y4&radius_km=50")
    assert r.status_code == 200
    html = r.data.decode()
    assert "H2X 1Y4 Montréal, Canada" in html      # libelle resolu
    assert "≈" in html and "km" in html             # puce distance
    assert '"radius_km": 50' in html                # centre carte transmis a _map.html


def test_pilots_page_near_not_found_shows_hint(client, fake_geocoder):
    r = client.get("/pilotes?near=nulle-part-xyz")
    assert r.status_code == 200
    assert ("introuvable" in r.data.decode()) or ("not found" in r.data.decode())


def test_missions_search_near_sorts_by_distance(client, make_user, fake_geocoder):
    import services
    c = make_user("near_cli", role="client")
    m_far = services.create_mission(
        c["id"], title="Loin Paris", description="x" * 20, mission_type="photo",
        country="France", city="Paris", lat=48.8566, lng=2.3522)
    m_close = services.create_mission(
        c["id"], title="Proche Montréal", description="x" * 20, mission_type="photo",
        country="Canada", city="Montréal", lat=45.52, lng=-73.60)
    r = client.get("/api/missions?near=H2X%201Y4")
    ids = [m["id"] for m in r.get_json()["missions"]]
    assert ids.index(m_close) < ids.index(m_far)


def test_search_radius_is_bounded(client, fake_geocoder):
    r = client.get("/api/pilotes?near=75011&radius_km=99999")
    assert r.status_code == 200
    assert r.get_json()["near"]["radius_km"] == 500


# ---------------------------------------------------------------------------
# Filtres confiance
# ---------------------------------------------------------------------------

def test_search_pilots_trust_filters(make_user, app_ctx):
    import services
    plain = make_user("trust_plain", role="pilot")
    verified = make_user("trust_verif", role="pilot")
    insured = make_user("trust_ins", role="pilot")
    cert_id = services.add_certification(
        verified["id"], authority="Transport Canada",
        title="Operations avancees (RPAS)", reference="TC-1")
    services.set_certification_verified(cert_id, True)
    services.add_certification(plain["id"], authority="DGAC", title="A2")  # non verifie
    services.upsert_pilot_profile(insured["id"], insurance=1,
                                  insurance_company="Hiscox", insurance_policy="P-1")

    ids = {p["id"] for p in services.search_pilots(only_verified=True, limit=500)}
    assert verified["id"] in ids and plain["id"] not in ids and insured["id"] not in ids

    ids = {p["id"] for p in services.search_pilots(only_insured=True, limit=500)}
    assert insured["id"] in ids and verified["id"] not in ids

    ids = {p["id"] for p in services.search_pilots(authority="Transport Canada", limit=500)}
    assert verified["id"] in ids and plain["id"] not in ids

    ids = {p["id"] for p in services.search_pilots(authority="DGAC", limit=500)}
    assert plain["id"] in ids and verified["id"] not in ids

    by_id = {p["id"]: p for p in services.search_pilots(limit=500)}
    assert by_id[verified["id"]]["verified_authorities"] == ["Transport Canada"]
    assert by_id[verified["id"]]["certs_verified"] == 1
    assert by_id[plain["id"]]["verified_authorities"] == []
    assert by_id[plain["id"]]["certs_total"] == 1


def test_api_pilots_accepts_trust_filters(client, make_user):
    import services
    with client.application.app_context():
        v = make_user("trust_api", role="pilot")
        cid = services.add_certification(v["id"], authority="FAA", title="Part 107")
        services.set_certification_verified(cid, True)
    r = client.get("/api/pilotes?only_verified=1&authority=FAA")
    assert r.status_code == 200
    ids = {p["id"] for p in r.get_json()["pilots"]}
    assert v["id"] in ids
    r = client.get("/pilotes?only_verified=1&only_insured=1&authority=FAA")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# FAQ / contact / sitemap / accueil
# ---------------------------------------------------------------------------

def test_faq_page_bilingual_with_structured_data(client):
    r = client.get("/faq")
    assert r.status_code == 200
    html = r.data.decode()
    assert '"@type": "FAQPage"' in html
    assert "Comment trouver un pilote" in html
    client.set_cookie("aube_lang", "en", domain="localhost.localdomain")
    r = client.get("/faq")
    assert "How do I find a drone pilot" in r.data.decode()


def test_faq_content_uses_real_platform_numbers():
    import content
    from config import PLATFORM_FEE_PCT, AUTO_RELEASE_DAYS
    entries = {e["id"]: e for e in content.faq("fr")}
    assert f"{int(PLATFORM_FEE_PCT)} %" in entries["cost"]["answer"]
    assert f"{AUTO_RELEASE_DAYS} jours" in entries["payment"]["answer"]
    assert "{" not in entries["cancel"]["answer"], "placeholder non formate"
    featured = content.faq("en", featured_only=True)
    assert 3 <= len(featured) <= 8 and all(e["featured"] for e in featured)


def test_home_shows_new_sections(client):
    html = client.get("/").data.decode()
    assert 'name="near"' in html                       # recherche code postal
    assert 'id="comment-ca-marche"' in html
    assert 'id="pourquoi"' in html and 'class="trust-grid"' in html
    assert 'id="rejoindre"' in html and 'class="join-card"' in html
    assert 'class="faq-item"' in html and 'href="/faq"' in html
    assert 'href="/contact"' in html
    assert '"@type": "FAQPage"' in html


def test_contact_get(client):
    r = client.get("/contact")
    assert r.status_code == 200
    html = r.data.decode()
    assert 'name="website"' in html                    # pot de miel present
    assert '"@type": "ContactPage"' in html


def _eml_count():
    from config import MAIL_DUMP_DIR
    return len(os.listdir(MAIL_DUMP_DIR)) if os.path.isdir(MAIL_DUMP_DIR) else 0


def test_contact_post_sends_admin_mail_and_ack(client):
    from config import MAIL_DUMP_DIR
    before = _eml_count()
    r = client.post("/contact", data={
        "name": "Jeanne Test", "email": "jeanne@example.org", "topic": "pilot",
        "message": "Bonjour, comment activer mes paiements Stripe ?",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    time.sleep(0.5)  # envoi asynchrone
    assert _eml_count() >= before + 2, "message equipe + accuse de reception"
    newest = sorted(os.listdir(MAIL_DUMP_DIR))[-2:]
    blob = b"".join(open(os.path.join(MAIL_DUMP_DIR, f), "rb").read() for f in newest)
    assert b"jeanne@example.org" in blob
    assert b"Reply-To: jeanne@example.org" in blob


def test_contact_post_honeypot_is_silently_dropped(client):
    before = _eml_count()
    r = client.post("/contact", data={
        "name": "Bot", "email": "bot@example.org", "topic": "other",
        "message": "buy cheap stuff now now now", "website": "http://spam.example",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)   # fait semblant d'accepter
    time.sleep(0.3)
    assert _eml_count() == before


def test_contact_post_validation_errors(client):
    before = _eml_count()
    r = client.post("/contact", data={
        "name": "X", "email": "pas-un-email", "topic": "nope", "message": "court",
    })
    assert r.status_code == 400
    html = r.data.decode()
    assert "invalide" in html or "Invalid" in html
    time.sleep(0.2)
    assert _eml_count() == before


def test_sitemap_and_robots_include_new_pages(client):
    xml = client.get("/sitemap.xml").data.decode()
    assert "/faq</loc>" in xml and "/contact</loc>" in xml


def test_footer_links_everywhere(client):
    html = client.get("/missions").data.decode()
    assert 'href="/faq"' in html and 'href="/contact"' in html
    assert "role=pilot" in html
