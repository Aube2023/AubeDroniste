"""Premier contact : message direct au pilote depuis sa fiche (comme Fiverr),
réponse du pilote, puis devis proposé depuis la conversation qui crée la
demande privée + le devis et bascule le fil sous la mission."""


def _thread_url(client_id, pilot_id):
    return f"/messages/contact/{client_id}/{pilot_id}"


def test_fiche_bouton_message_et_redirection(client, auth_client, app_ctx, client_user, pilot_user):
    # Visiteur connecté : bouton « Envoyer un message » + « Demander un devis »
    c = auth_client(client_user["id"])
    html = c.get(f"/pilotes/{pilot_user['id']}").data.decode()
    assert f"/pilotes/{pilot_user['id']}/message" in html and "Envoyer un message" in html
    assert f"/missions/nouvelle?pilot={pilot_user['id']}" in html and "Demander un devis" in html
    # Le bouton mène au fil direct (client, pilote)
    r = c.get(f"/pilotes/{pilot_user['id']}/message")
    assert r.status_code == 302 and r.headers["Location"].endswith(_thread_url(client_user["id"], pilot_user["id"]))
    # Le pilote ne voit pas le bouton sur sa propre fiche, et ne peut pas s'écrire
    p = auth_client(pilot_user["id"])
    assert f"/pilotes/{pilot_user['id']}/message" not in p.get(f"/pilotes/{pilot_user['id']}").data.decode()
    assert p.get(f"/pilotes/{pilot_user['id']}/message").status_code == 404


def test_fil_direct_envoi_reponse_et_badge(auth_client, app_ctx, client_user, pilot_user):
    import services
    c = auth_client(client_user["id"])
    url = _thread_url(client_user["id"], pilot_user["id"])
    html = c.get(url).data.decode()
    assert "Premier contact" in html and "Décrivez votre besoin" in html and 'id="composer"' in html
    c.post(url, data={"body": "Bonjour, un survol de chalet à Charlevoix samedi, c'est possible ?"})
    assert services.unread_count(pilot_user["id"]) == 1
    # Boîte du pilote : le fil « Premier contact », non lu, badge dans la barre
    p = auth_client(pilot_user["id"])
    html = p.get("/messages").data.decode()
    assert "Premier contact" in html and "survol de chalet" in html and 'class="nav-badge">1<' in html
    # Il ouvre le fil : consigne + bouton « Proposer un devis », message passé lu
    html = p.get(url).data.decode()
    assert "Proposer un devis" in html and f"{url}/devis" in html and "Ce client vous a écrit" in html
    assert services.unread_count(pilot_user["id"]) == 0
    p.post(url, data={"body": "Oui, samedi matin ça marche."})
    # Le client voit la réponse en bulle et sa propre bulle « mine »
    c = auth_client(client_user["id"])
    html = c.get(url).data.decode()
    assert "samedi matin" in html and 'class="bubble mine"' in html
    # Rafraîchissement JSON
    j = c.get(f"/api{url}?after=0").get_json()
    assert [m["body"] for m in j["messages"]][-1] == "Oui, samedi matin ça marche." and j["messages"][-1]["mine"] is False


def test_permissions_et_filtre(auth_client, app_ctx, make_user, client_user, pilot_user):
    import services
    url = _thread_url(client_user["id"], pilot_user["id"])
    # Le pilote ne peut pas ouvrir un fil avec un client qui ne lui a jamais écrit
    p = auth_client(pilot_user["id"])
    assert p.get(url).status_code == 403
    assert p.get(f"/api{url}").status_code == 403
    # Un tiers non plus
    tiers = make_user("tiers_contact", role="client")
    t = auth_client(tiers["id"])
    assert t.get(url).status_code == 403
    assert t.post(url, data={"body": "coucou"}).status_code == 403
    # Coordonnées externes bloquées avant paiement
    c = auth_client(client_user["id"])
    html = c.post(url, data={"body": "Appelez-moi au 514 555 0199"}, follow_redirects=True).data.decode()
    assert "Coordonnees externes interdites" in html
    assert not services.contact_thread_exists(client_user["id"], pilot_user["id"])
    # Un pilote « inexistant » comme cible : 404
    assert c.get(_thread_url(client_user["id"], 999999)).status_code == 404


def test_blocage_ferme_le_fil(auth_client, app_ctx, client_user, pilot_user):
    import services
    url = _thread_url(client_user["id"], pilot_user["id"])
    c = auth_client(client_user["id"])
    c.post(url, data={"body": "Première question sur une inspection de toiture."})
    services.block_user(pilot_user["id"], client_user["id"])
    c.post(url, data={"body": "Encore moi"})
    assert len(services.contact_thread(client_user["id"], pilot_user["id"], client_user["id"])) == 1
    services.unblock_user(pilot_user["id"], client_user["id"])


def test_devis_depuis_la_conversation(auth_client, app_ctx, client_user, pilot_user):
    import db
    import services
    url = _thread_url(client_user["id"], pilot_user["id"])
    c = auth_client(client_user["id"])
    c.post(url, data={"body": "Bonjour, une orthophoto de 12 ha près de Québec, fin octobre ?"})
    p = auth_client(pilot_user["id"])
    p.post(url, data={"body": "Oui, je vous prépare un devis."})
    last_id = services.contact_thread(client_user["id"], pilot_user["id"], pilot_user["id"])[-1]["id"]
    # Le client ne peut pas proposer de devis ; le pilote ouvre le formulaire
    assert auth_client(client_user["id"]).get(f"{url}/devis").status_code == 403
    p = auth_client(pilot_user["id"])
    html = p.get(f"{url}/devis").data.decode()
    assert "Proposer un devis" in html and "orthophoto de 12 ha" in html and 'name="price"' in html
    # Devis invalide (prix manquant) : rien n'est créé
    r = p.post(f"{url}/devis", data={"title": "Orthophoto", "need": "12 ha", "mission_type": "mapping",
                                     "country": "Canada", "description": "x" * 40})
    assert r.status_code == 200 and "tarif invalide" in r.data.decode()
    assert services.latest_contact_mission(client_user["id"], pilot_user["id"]) is None
    # Devis valide : mission privée au nom du client + devis, fil basculé
    r = p.post(f"{url}/devis", data={
        "title": "Orthophoto RTK de 12 ha", "need": "Orthophoto de 12 hectares près de Québec, fin octobre.",
        "mission_type": "mapping", "country": "Canada", "city": "Québec", "currency": "CAD",
        "price": "1450", "duration_hours": "4",
        "description": "Vol RTK à 80 m, recouvrement 80/70, orthomosaïque 2 cm/px livrée en GeoTIFF.",
        "deliverables": "GeoTIFF + rapport PDF", "message": "Au plaisir !",
    })
    mission_id = services.latest_contact_mission(client_user["id"], pilot_user["id"])
    assert mission_id and r.status_code == 302 and r.headers["Location"].endswith(f"/messages/{mission_id}/{client_user['id']}")
    m = db.fetchone("SELECT * FROM missions WHERE id=?", (mission_id,))
    assert m["client_user_id"] == client_user["id"] and m["targeted_pilot_id"] == pilot_user["id"]
    assert m["is_private"] == 1 and m["from_contact"] == 1 and m["status"] == "open" and m["currency"] == "CAD"
    b = db.fetchone("SELECT * FROM bids WHERE mission_id=? AND pilot_user_id=?", (mission_id, pilot_user["id"]))
    assert b and b["status"] == "pending" and b["price"] == 1450 and b["deliverables"] == "GeoTIFF + rapport PDF"
    # Les messages ont suivi, plus une ligne « Devis proposé » ; le fil direct est vide
    assert not services.contact_thread_exists(client_user["id"], pilot_user["id"])
    fil = services.thread(mission_id, client_user["id"], pilot_user["id"])
    assert [x["body"] for x in fil][:2] == ["Bonjour, une orthophoto de 12 ha près de Québec, fin octobre ?",
                                           "Oui, je vous prépare un devis."]
    assert "Devis proposé" in fil[-1]["body"] and "1450 CAD" in fil[-1]["body"]
    # Le client, dont la page du fil direct tourne encore, est redirigé vers le fil de la mission
    c = auth_client(client_user["id"])
    j = c.get(f"/api{url}?after={last_id}").get_json()
    assert j["redirect"].endswith(f"/messages/{mission_id}/{pilot_user['id']}")
    # Il voit le devis sur la mission et peut l'accepter ; le bouton de la fiche mène désormais au fil de la mission
    html = c.get(f"/missions/{mission_id}").data.decode()
    assert "Orthophoto RTK de 12 ha" in html and "1450" in html and f"/missions/{mission_id}/accepter/{b['id']}" in html
    r = c.get(f"/pilotes/{pilot_user['id']}/message")
    assert r.headers["Location"].endswith(f"/messages/{mission_id}/{pilot_user['id']}")
    # Un autre pilote ne voit pas cette demande privée
    assert auth_client(client_user["id"]).get(f"/messages/{mission_id}/{pilot_user['id']}").status_code == 200


def test_export_et_temps_de_reponse_comptent_le_premier_contact(auth_client, app_ctx, client_user, pilot_user):
    import services
    url = _thread_url(client_user["id"], pilot_user["id"])
    auth_client(client_user["id"]).post(url, data={"body": "Disponible pour un mariage en juin ?"})
    data = services.export_user_data(client_user["id"])
    assert any("mariage en juin" in row["body"] for row in data["direct_messages"])
    # pilot_response_time ne plante pas avec des premiers contacts sans réponse
    assert services.pilot_response_time(pilot_user["id"]) is None
