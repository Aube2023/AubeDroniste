"""Signaler et bloquer (exigences Google Play pour le contenu utilisateur) :
le signalement part en base et vers l'admin, le blocage ferme la messagerie
et les devis dans les deux sens, l'admin traite depuis /admin/signalements."""


def test_signaler_un_profil_et_une_mission(auth_client, app_ctx, client_user, pilot_user, open_mission):
    import services
    c = auth_client(client_user["id"])
    # Le bouton est sur la fiche pilote et sur la mission d'un autre, pas sur la sienne
    html = c.get(f"/pilotes/{pilot_user['id']}").data.decode()
    assert 'name="target_type" value="user"' in html and "Envoyer le signalement" in html
    html = c.get(f"/missions/{open_mission}").data.decode()
    assert 'name="target_type" value="mission"' not in html  # sa propre mission
    n0 = services.count_reports_open()   # la base de test est partagée entre les tests
    r = c.post("/signaler", data={"target_type": "user", "target_id": pilot_user["id"],
                                  "reason": "spam", "details": "Publicité en boucle"})
    assert r.status_code == 302
    last = services.list_reports("open")[0]
    assert services.count_reports_open() == n0 + 1 and last["target_user_id"] == pilot_user["id"]
    assert last["reason"] == "spam" and last["details"] == "Publicité en boucle"
    # Doublon dans les 24 h : refusé sans nouvelle ligne
    c.post("/signaler", data={"target_type": "user", "target_id": pilot_user["id"], "reason": "spam"})
    assert services.count_reports_open() == n0 + 1
    # Le pilote signale la mission du client
    p = auth_client(pilot_user["id"])
    html = p.get(f"/missions/{open_mission}").data.decode()
    assert 'name="target_type" value="mission"' in html
    p.post("/signaler", data={"target_type": "mission", "target_id": open_mission, "reason": "scam"})
    assert services.count_reports_open() == n0 + 2
    # Motif inconnu ou cible inconnue : refus
    assert p.post("/signaler", data={"target_type": "user", "target_id": client_user["id"], "reason": "xxx"}).status_code == 400
    assert p.post("/signaler", data={"target_type": "user", "target_id": 999999, "reason": "spam"}).status_code == 404


def test_signaler_un_fil_reserve_aux_parties(auth_client, app_ctx, client_user, pilot_user, open_mission,
                                            pending_bid, make_user):
    import services
    p = auth_client(pilot_user["id"])
    html = p.get(f"/messages/{open_mission}/{client_user['id']}").data.decode()
    assert 'name="target_type" value="thread"' in html and f'name="peer_id" value="{client_user["id"]}"' in html
    n0 = services.count_reports_open()
    r = p.post("/signaler", data={"target_type": "thread", "target_id": open_mission,
                                  "peer_id": client_user["id"], "reason": "harassment"})
    assert r.status_code == 302 and services.count_reports_open() == n0 + 1
    tiers = make_user("tiers_sig", role="pilot")
    t = auth_client(tiers["id"])
    assert t.post("/signaler", data={"target_type": "thread", "target_id": open_mission,
                                     "peer_id": client_user["id"], "reason": "spam"}).status_code == 403


def test_bloquer_ferme_messages_et_devis(auth_client, app_ctx, client_user, pilot_user, open_mission,
                                         pending_bid, make_user):
    import services
    c = auth_client(client_user["id"])
    # Avant : le fil est ouvert, avec le bouton Bloquer
    html = c.get(f"/messages/{open_mission}/{pilot_user['id']}").data.decode()
    assert 'id="composer"' in html and f"/bloquer/{pilot_user['id']}" in html
    assert c.post(f"/bloquer/{pilot_user['id']}").status_code == 302
    assert services.has_blocked(client_user["id"], pilot_user["id"])
    assert services.is_blocked_between(pilot_user["id"], client_user["id"])
    # Le bloqueur voit l'avis et Débloquer, plus de composeur
    html = c.get(f"/messages/{open_mission}/{pilot_user['id']}").data.decode()
    assert 'id="composer"' not in html and "Vous avez bloqué cette personne" in html
    assert f"/debloquer/{pilot_user['id']}" in html
    # La personne bloquée ne peut plus écrire ni voir le composeur
    p = auth_client(pilot_user["id"])
    html = p.get(f"/messages/{open_mission}/{client_user['id']}").data.decode()
    assert 'id="composer"' not in html and "Cette conversation est fermée" in html
    before = len(services.thread(open_mission, pilot_user["id"], client_user["id"]))
    p.post(f"/missions/{open_mission}/messages", data={"peer_id": client_user["id"], "body": "Coucou"})
    assert len(services.thread(open_mission, pilot_user["id"], client_user["id"])) == before
    # Ni déposer de devis sur une autre mission de ce client
    mid2 = services.create_mission(client_user["id"], title="Suivi de chantier", description="Photos hebdo",
                                   mission_type="immobilier", country="France", city="Lyon",
                                   lat=45.75, lng=4.85, budget_min=300, budget_max=900)
    p.post(f"/missions/{mid2}/enchere", data={"price": 500,
                                               "description": "Un devis détaillé de plus de trente caractères."})
    import db
    assert db.fetchone("SELECT 1 FROM bids WHERE mission_id=? AND pilot_user_id=?", (mid2, pilot_user["id"])) is None
    # On ne peut pas se bloquer soi-même ; la liste des bloqués est dans les paramètres
    # (auth_client réutilise le même test_client : on se reconnecte en client)
    assert not services.block_user(client_user["id"], client_user["id"])
    c = auth_client(client_user["id"])
    html = c.get("/espace/parametres").data.decode()
    assert "Personnes bloquées" in html and f"/debloquer/{pilot_user['id']}" in html
    # Débloquer rouvre tout
    c.post(f"/debloquer/{pilot_user['id']}")
    assert not services.is_blocked_between(client_user["id"], pilot_user["id"])
    p = auth_client(pilot_user["id"])
    assert 'id="composer"' in p.get(f"/messages/{open_mission}/{client_user['id']}").data.decode()


def test_admin_traite_les_signalements(auth_client, app_ctx, make_user, client_user, pilot_user):
    import db
    import services
    admin = make_user("admin_sig", role="client")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    rid = services.create_report(reporter_id=client_user["id"], target_type="user", target_id=pilot_user["id"],
                                 target_user_id=pilot_user["id"], reason="fake", details="Photos volées")
    # Un simple utilisateur n'entre pas ; l'admin voit le compteur et la fiche
    assert auth_client(client_user["id"]).get("/admin/signalements").status_code in (302, 403, 404)
    a = auth_client(admin["id"])
    html = a.get("/espace").data.decode()
    assert "Signalements" in html and "à traiter" in html
    html = a.get("/admin/signalements").data.decode()
    assert "Faux profil ou usurpation" in html and "Photos volées" in html
    n0 = services.count_reports_open()
    a.post(f"/admin/signalements/{rid}/statut", data={"status": "handled"})
    assert services.count_reports_open() == n0 - 1
    handled = [r for r in services.list_reports("handled") if r["id"] == rid][0]
    assert handled["handled_at"] and "Photos volées" not in a.get("/admin/signalements").data.decode()
    assert "Photos volées" in a.get("/admin/signalements?statut=handled").data.decode()
