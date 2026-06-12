"""Forfaits pilote (/espace/pilote/forfaits) : rendu + cycle de vie complet.

Régression : la page renvoyait 500 (le template déballait drone_capabilities
en paires (code, libellé) alors que config.DRONE_CAPABILITIES est une liste
plate) -> le lien "Gérer mes forfaits" du profil pilote était mort.
"""

# IMPORTANT : aucun import de `db`/`services`/`app` au niveau module (voir
# conftest : la config DB est figée à l'import). Imports dans les tests.


def test_packages_page_renders_for_pilot(make_user, auth_client):
    """GET /espace/pilote/forfaits -> 200 pour un pilote (régression 500)."""
    u = make_user("pkgpilot", role="pilot")
    c = auth_client(u["id"])
    r = c.get("/espace/pilote/forfaits")
    assert r.status_code == 200
    assert "Mes forfaits" in r.get_data(as_text=True)


def test_packages_page_forbidden_for_client(make_user, auth_client):
    """Un client simple (non pilote) -> 403."""
    u = make_user("pkgclient", role="client")
    c = auth_client(u["id"])
    assert c.get("/espace/pilote/forfaits").status_code == 403


def test_package_create_public_toggle_delete(make_user, auth_client, app_ctx):
    """Cycle complet : création (capacités multiples), affichage public,
    masquage, suppression."""
    import db

    u = make_user("pkgcycle", role="pilot")
    c = auth_client(u["id"])

    r = c.post("/espace/pilote/forfaits", data={
        "title": "Captation mariage demi-journée",
        "description": "4h de captation aérienne, drone 4K, livraison 7 jours.",
        "price": "1200", "currency": "EUR", "mission_type": "mariage",
        "duration_hours": "4", "deliverables": "100 photos + film 3 min",
        "capabilities": ["camera_4k", "zoom_optique"],
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "Captation mariage" in r.get_data(as_text=True)

    pkg = db.fetchone(
        "SELECT * FROM pilot_packages WHERE pilot_user_id=?", (u["id"],))
    assert pkg is not None
    assert pkg["capabilities"] == "camera_4k,zoom_optique"

    # Visible sur la page publique du pilote
    r = c.get(f"/pilotes/{u['id']}")
    assert r.status_code == 200
    assert "Captation mariage" in r.get_data(as_text=True)

    # Masquer puis supprimer
    c.post(f"/espace/pilote/forfaits/{pkg['id']}/toggle")
    row = db.fetchone(
        "SELECT is_active FROM pilot_packages WHERE id=?", (pkg["id"],))
    assert row is not None and row["is_active"] == 0

    c.post(f"/espace/pilote/forfaits/{pkg['id']}/supprimer")
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM pilot_packages WHERE pilot_user_id=?",
        (u["id"],))
    assert row is not None and row["n"] == 0
