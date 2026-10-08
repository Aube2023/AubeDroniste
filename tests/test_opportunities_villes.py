"""Emplois : filtre par ville (demande du 2026-10-08), sous le pays et la
province, avec le nombre d'offres de chaque ville."""
import pytest


@pytest.fixture()
def offres(app_ctx):
    import db
    rows = [("v1", "Islande", "Höfuðborgarsvæðið", "Reykjavik"), ("v2", "Islande", "Höfuðborgarsvæðið", "Reykjavik"),
            ("v3", "Islande", "Höfuðborgarsvæðið", "Kópavogur"), ("v4", "Islande", "Norðurland", "Akureyri"),
            ("v5", "Islande", "Norðurland", "")]
    for ref, country, region, city in rows:
        db.execute("INSERT INTO opportunities (source, source_ref, kind, title_fr, title_en, url_fr, country, region, "
                   "city, status, first_seen_at, last_seen_at) VALUES ('jobbank', ?, 'job', ?, ?, 'https://x.example/', "
                   "?, ?, ?, 'published', '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
                   ("villes-" + ref, "Poste " + ref, "Job " + ref, country, region, city))
    yield
    db.execute("DELETE FROM opportunities WHERE source_ref LIKE 'villes-%'")


def _titles(html):
    return sorted(t for t in ("Poste v1", "Poste v2", "Poste v3", "Poste v4", "Poste v5") if t in html)


def test_liste_des_villes_du_pays(client, offres):
    html = client.get("/emplois?country=Islande").data.decode()
    assert 'name="city"' in html
    assert ">Akureyri (1)</option>" in html and ">Kópavogur (1)</option>" in html and ">Reykjavik (2)</option>" in html
    assert html.index(">Akureyri") < html.index(">Kópavogur") < html.index(">Reykjavik")   # ordre alphabétique
    assert _titles(html) == ["Poste v1", "Poste v2", "Poste v3", "Poste v4", "Poste v5"]


def test_filtre_par_ville(client, offres):
    html = client.get("/emplois?country=Islande&city=Reykjavik").data.decode()
    assert _titles(html) == ["Poste v1", "Poste v2"]
    assert '<option value="Reykjavik" selected>' in html


def test_villes_de_la_province_choisie(client, offres):
    html = client.get("/emplois?country=Islande&region=Norðurland").data.decode()
    # Une seule ville dans cette province : pas de liste à un seul choix.
    assert 'name="city"' not in html
    html = client.get("/emplois?country=Islande&region=Höfuðborgarsvæðið").data.decode()
    assert ">Reykjavik (2)</option>" in html and "Akureyri" not in html.split('name="city"')[1].split("</select>")[0]


def test_ville_inconnue_ou_sans_pays_ignoree(client, offres):
    # Ville d'une autre province : ignorée, pas de liste vide en silence.
    html = client.get("/emplois?country=Islande&region=Norðurland&city=Reykjavik").data.decode()
    assert _titles(html) == ["Poste v4", "Poste v5"]
    # Sans pays, pas de filtre ville.
    assert 'name="city"' not in client.get("/emplois?city=Reykjavik").data.decode()


def test_changer_de_pays_remet_province_et_ville(client, offres):
    html = client.get("/emplois?country=Islande").data.decode()
    assert 'name="country" data-action="submit-on-change-select" data-resets="region city"' in html
    assert 'name="region" data-action="submit-on-change-select" data-resets="city"' in html
