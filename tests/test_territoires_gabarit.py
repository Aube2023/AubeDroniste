"""Territoires opérés : une boucle `{% for t in … %}` masquait la fonction de
traduction `t()` dans le gabarit, et /espace/pilote tombait en 500
(« 'dict' object is not callable ») dès qu'un pilote avait un territoire
(production, 2026-09-21)."""
import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "templates"


def test_espace_pilote_avec_territoires(make_user, auth_client, app_ctx, client):
    import services
    u = make_user("terr_pilote", role="pilot")
    services.set_pilot_territories(u["id"], [{"country": "CA", "region": "Québec"}])
    r = auth_client(u["id"]).get("/espace/pilote")
    assert r.status_code == 200
    html = r.data.decode()
    assert 'name="territory_country"' in html and 'value="CA"' in html and 'value="Québec"' in html
    fiche = client.get(f"/pilotes/{u['id']}")
    assert fiche.status_code == 200 and "Québec" in fiche.data.decode()


def test_aucun_gabarit_ne_masque_t():
    motif = re.compile(r"\{%-?\s*(?:for|set|with|macro)\s+[^%]*?\bt\b\s*(?:,|=|\bin\b)")
    fautifs = [f"{p.relative_to(TEMPLATES)}:{i}"
               for p in TEMPLATES.rglob("*.html")
               for i, ligne in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
               if motif.search(ligne)]
    assert not fautifs, "variable « t » qui masque la traduction t() : " + ", ".join(fautifs)
