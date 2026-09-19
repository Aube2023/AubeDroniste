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


def test_forfait_sur_devis_et_specialite_deduite(make_user, auth_client, app_ctx):
    """Prix 0 = « sur devis » : accepté à la création, affiché sans « 0 CAD »,
    et la mission préremplie n'a pas de budget. Le type du forfait devient une
    spécialité du pilote (visible sur sa fiche, filtrable)."""
    import db
    u = make_user("pkg_devis", role="pilot")
    c = auth_client(u["id"])
    r = c.post("/espace/pilote/forfaits", data={
        "title": "Photogrammétrie de chantier",
        "description": "Captation par drone RTK, remise des données brutes sans traitement.",
        "price": "0", "currency": "CAD", "mission_type": "3d",
    }, follow_redirects=True)
    assert r.status_code == 200
    pkg = db.fetchone("SELECT * FROM pilot_packages WHERE pilot_user_id=?", (u["id"],))
    assert pkg is not None and pkg["price"] == 0
    html = c.get(f"/pilotes/{u['id']}").get_data(as_text=True)
    assert "Sur devis" in html and "0 <span class=\"cur\">" not in html
    assert "Spécialités non renseignées" not in html
    assert db.fetchone("SELECT 1 FROM pilot_specialties WHERE pilot_user_id=? AND mission_type='3d'",
                       (u["id"],)) is not None
    # Un client réserve : budget vide, pas « 0 »
    client_u = make_user("pkg_devis_client", role="client")
    html = auth_client(client_u["id"]).get(f"/missions/nouvelle?package={pkg['id']}&pilot={u['id']}").get_data(as_text=True)
    assert 'name="budget_min" value="0"' not in html
    # Prix négatif refusé
    r = auth_client(u["id"]).post("/espace/pilote/forfaits", data={
        "title": "Négatif", "description": "Description suffisamment longue pour passer.",
        "price": "-5", "currency": "CAD", "mission_type": "photo"}, follow_redirects=True)
    assert db.fetchone("SELECT COUNT(*) AS n FROM pilot_packages WHERE pilot_user_id=?", (u["id"],))["n"] == 1


def _mission_form(**extra):
    base = {"title": "Photogrammétrie du Vieux-Port", "description": "Captation des données brutes par drone, remise sans traitement.",
            "mission_type": "3d", "country": "Canada", "city": "Montréal", "currency": "CAD",
            "budget_min": "600", "budget_max": "1100"}
    base.update(extra)
    return base


def test_reservation_forfait_notifie_le_pilote_vise(client, auth_client, make_user, monkeypatch):
    """« Réserver » un forfait publie une mission ciblée : le pilote visé reçoit
    un courriel de demande directe, et personne d'autre n'est alerté."""
    import mailer, db, services
    sent, broadcast = [], []
    monkeypatch.setattr(mailer, "send", lambda **kw: sent.append(kw) or True)
    monkeypatch.setattr(mailer, "send_mission_alerts", lambda *a, **k: broadcast.append(a) or 0)
    pilot = make_user("pkg_notif_pilot", role="pilot", country="Canada", city="Québec", lat=46.8, lng=-71.2)
    with client.application.app_context():
        services.upsert_pilot_profile(pilot["id"], is_available=1)
        pkg_id = services.create_pilot_package(pilot["id"], title="Photogrammétrie", description="Captation des données brutes uniquement.",
                                               price=0, currency="CAD", mission_type="3d")
    cli = make_user("pkg_notif_client", role="client")
    r = auth_client(cli["id"]).post("/missions/nouvelle", data=_mission_form(
        targeted_pilot_id=str(pilot["id"]), from_package_id=str(pkg_id)), follow_redirects=True)
    assert r.status_code == 200
    with client.application.app_context():
        m = db.fetchone("SELECT id, targeted_pilot_id, from_package_id, status FROM missions WHERE client_user_id=?", (cli["id"],))
    assert m["targeted_pilot_id"] == pilot["id"] and m["from_package_id"] == pkg_id and m["status"] == "open"
    assert broadcast == []                      # pas de diffusion aux autres pilotes
    assert len(sent) == 1 and sent[0]["to"] == pilot["email"] and sent[0]["template"] == "mission_request"
    assert sent[0]["context"]["package"]["title"] == "Photogrammétrie"
    # Le pilote visé voit le bandeau ; la demande (privée par défaut) est
    # introuvable pour un autre pilote.
    html = auth_client(pilot["id"]).get(f"/missions/{m['id']}").get_data(as_text=True)
    assert "vous est réservée" in html
    other = make_user("pkg_notif_other", role="pilot")
    assert auth_client(other["id"]).get(f"/missions/{m['id']}").status_code == 404


def test_reservation_forfait_pilote_bloque_non_notifie(client, auth_client, make_user, monkeypatch):
    import mailer, services
    sent = []
    monkeypatch.setattr(mailer, "send", lambda **kw: sent.append(kw) or True)
    monkeypatch.setattr(mailer, "send_mission_alerts", lambda *a, **k: 0)
    pilot = make_user("pkg_block_pilot", role="pilot", country="Canada")
    cli = make_user("pkg_block_client", role="client")
    with client.application.app_context():
        services.block_user(pilot["id"], cli["id"])
    auth_client(cli["id"]).post("/missions/nouvelle", data=_mission_form(targeted_pilot_id=str(pilot["id"])), follow_redirects=True)
    assert sent == []


def test_courriel_demande_directe_se_rend(client, make_user):
    """Les gabarits HTML et texte de la demande directe se rendent (dump .eml en test)."""
    import os, mailer
    from config import MAIL_DUMP_DIR
    pilot = make_user("pkg_render_pilot", role="pilot", country="Canada")
    mission = {"id": 42, "title": "Photogrammétrie du Vieux-Port", "mission_type": "3d", "city": "Montréal",
               "country": "Canada", "budget_min": 600, "budget_max": 1100, "currency": "CAD", "is_urgent": 0}
    before = set(os.listdir(MAIL_DUMP_DIR)) if os.path.isdir(MAIL_DUMP_DIR) else set()
    with client.application.app_context():
        assert mailer.send_mission_request({"email": pilot["email"], "full_name": "Benoit Leroux"}, mission,
                                           {"title": "Photogrammétrie"}, async_=False)
    new = [f for f in os.listdir(MAIL_DUMP_DIR) if f not in before]
    assert new, "aucun .eml produit"
    raw = open(os.path.join(MAIL_DUMP_DIR, new[-1]), "rb").read().decode("utf-8", "replace")
    assert pilot["email"] in raw and "/missions/42" in raw


def test_reservation_privee_par_defaut_invisible_des_autres(client, auth_client, make_user, monkeypatch):
    """« Réserver » = demande réservée au pilote visé : absente de /missions, de
    l'API, de la carte et de l'accueil ; 404 pour un autre pilote ; devis d'un
    autre pilote refusé ; visible du client et du pilote visé (tableau de bord)."""
    import mailer, db
    monkeypatch.setattr(mailer, "send", lambda **kw: True)
    broadcast = []
    monkeypatch.setattr(mailer, "send_mission_alerts", lambda *a, **k: broadcast.append(a) or 0)
    pilot = make_user("priv_pilot", role="pilot", country="Canada", lat=46.8, lng=-71.2)
    other = make_user("priv_other", role="pilot", country="Canada", lat=46.9, lng=-71.3)
    cli = make_user("priv_client", role="client")
    title = "Demande privée toiture Limoilou"
    auth_client(cli["id"]).post("/missions/nouvelle", data=_mission_form(
        title=title, targeted_pilot_id=str(pilot["id"])), follow_redirects=True)
    with client.application.app_context():
        m = db.fetchone("SELECT id, is_private FROM missions WHERE title=?", (title,))
    assert m["is_private"] == 1 and broadcast == []
    assert title not in client.get("/missions?country=Canada").get_data(as_text=True)
    assert all(x["id"] != m["id"] for x in client.get("/api/missions?country=Canada").get_json()["missions"])
    assert all(x["id"] != m["id"] for x in client.get("/api/map?country=Canada").get_json()["missions"])
    assert title not in client.get("/").get_data(as_text=True)
    anon = client.application.test_client()                                          # sans cookie
    assert anon.get(f"/missions/{m['id']}", headers={"User-Agent": "Mozilla/5.0 pytest"}).status_code == 404
    assert auth_client(other["id"]).get(f"/missions/{m['id']}").status_code == 404     # autre pilote
    assert auth_client(other["id"]).post(f"/missions/{m['id']}/enchere", data={"price": "500", "message": "x"}).status_code == 404
    assert auth_client(cli["id"]).get(f"/missions/{m['id']}").status_code == 200
    html = auth_client(pilot["id"]).get(f"/missions/{m['id']}").get_data(as_text=True)
    assert "vous est réservée" in html and "demande privée" in html
    assert title in auth_client(pilot["id"]).get("/espace").get_data(as_text=True)


def test_reservation_ouverte_aux_autres_sur_choix(client, auth_client, make_user, monkeypatch):
    import mailer, db
    sent, broadcast = [], []
    monkeypatch.setattr(mailer, "send", lambda **kw: sent.append(kw) or True)
    monkeypatch.setattr(mailer, "send_mission_alerts", lambda recipients, mission, **k: broadcast.append([r["id"] for r in recipients]) or 0)
    pilot = make_user("open_pilot", role="pilot", country="Canada", city="Québec", lat=46.8, lng=-71.2)
    cli = make_user("open_client", role="client")
    title = "Demande ouverte façade Sillery"
    auth_client(cli["id"]).post("/missions/nouvelle", data=_mission_form(
        title=title, targeted_pilot_id=str(pilot["id"]), visibility="open"), follow_redirects=True)
    with client.application.app_context():
        m = db.fetchone("SELECT id, is_private FROM missions WHERE title=?", (title,))
    assert m["is_private"] == 0
    assert title in client.get("/missions?country=Canada").get_data(as_text=True)
    assert len(sent) == 1 and sent[0]["to"] == pilot["email"]        # demande directe
    assert len(broadcast) == 1 and pilot["id"] not in broadcast[0]   # diffusion sans doublon au pilote visé


def test_nom_complet_du_pilote_affiche_partout(client, make_user):
    """Décision 2026-09-19 : le pilote apparaît sous son nom complet (annuaire,
    accueil, fiche), sans mention « identité révélée après paiement »."""
    import services
    u = make_user("nom_complet", role="pilot", country="Canada", city="Québec", full_name="Benoit Leroux")
    with client.application.app_context():
        services.upsert_pilot_profile(u["id"], is_available=1)
    fiche = client.get(f"/pilotes/{u['id']}").get_data(as_text=True)
    assert "Benoit Leroux" in fiche and "Benoit L." not in fiche
    assert "révélée après" not in fiche and "Québec" in fiche
    assert "Benoit Leroux" in client.get("/pilotes?country=Canada").get_data(as_text=True)
