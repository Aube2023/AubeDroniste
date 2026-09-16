"""Messagerie : boîte de réception, fil en bulles, rafraîchissement JSON,
badge non lus, et les mêmes garde-fous que l'envoi (tiers refusé,
coordonnées bloquées avant paiement)."""


def test_boite_fil_et_badge(client, auth_client, app_ctx, open_mission, pending_bid, client_user, pilot_user):
    import services
    # Le pilote écrit au client sur la mission où il a déposé un devis
    services.send_message(mission_id=open_mission, sender_user_id=pilot_user["id"],
                          recipient_user_id=client_user["id"], body="Bonjour, je suis disponible mardi.")
    # Le client voit la conversation dans sa boîte, non lue, badge dans la barre
    c = auth_client(client_user["id"])
    html = c.get("/messages").data.decode()
    assert 'class="inbox' in html and "Tournage immobilier" in html and "disponible mardi" in html
    assert 'class="nav-badge">1<' in html
    # Il ouvre le fil : bulles, verrou avant paiement, composeur ; le message passe lu
    html = c.get(f"/messages/{open_mission}/{pilot_user['id']}").data.decode()
    assert 'class="bubble ' in html and "disponible mardi" in html
    assert "bloqués dans la messagerie" in html and 'id="composer"' in html
    assert services.unread_count(client_user["id"]) == 0
    # Il répond depuis le composeur, le pilote voit la bulle « mine » chez lui
    c.post(f"/missions/{open_mission}/messages", data={"peer_id": pilot_user["id"], "body": "Parfait, mardi 10 h."})
    p = auth_client(pilot_user["id"])
    html = p.get(f"/messages/{open_mission}/{client_user['id']}").data.decode()
    assert "Parfait, mardi 10" in html and 'class="bubble mine"' in html


def test_rafraichissement_json_et_permissions(client, auth_client, app_ctx, open_mission, pending_bid,
                                             client_user, pilot_user, make_user):
    import services
    services.send_message(mission_id=open_mission, sender_user_id=client_user["id"],
                          recipient_user_id=pilot_user["id"], body="Premier")
    p = auth_client(pilot_user["id"])
    r = p.get(f"/api/messages/{open_mission}/{client_user['id']}?after=0")
    assert r.status_code == 200
    j = r.get_json()
    assert len(j["messages"]) == 1 and j["messages"][0]["body"] == "Premier" and j["messages"][0]["mine"] is False
    last = j["messages"][0]["id"]
    assert p.get(f"/api/messages/{open_mission}/{client_user['id']}?after={last}").get_json()["messages"] == []
    # Un tiers sans devis ne peut ni lire le fil ni le rafraîchir
    tiers = make_user("tiers_msg", role="pilot")
    t = auth_client(tiers["id"])
    assert t.get(f"/messages/{open_mission}/{client_user['id']}").status_code == 403
    assert t.get(f"/api/messages/{open_mission}/{client_user['id']}").status_code == 403
    # Anonyme : redirigé vers la connexion
    assert client.application.test_client().get("/messages").status_code in (302, 401, 403)


def test_liste_vide_et_liens_depuis_mission(auth_client, app_ctx, make_user, open_mission, pending_bid,
                                            client_user, pilot_user):
    import services
    seul = make_user("seul_msg", role="client")
    html = auth_client(seul["id"]).get("/messages").data.decode()
    assert "Aucune conversation pour l" in html
    services.send_message(mission_id=open_mission, sender_user_id=pilot_user["id"],
                          recipient_user_id=client_user["id"], body="Question sur le devis")
    html = auth_client(client_user["id"]).get(f"/missions/{open_mission}").data.decode()
    assert f"/messages/{open_mission}/{pilot_user['id']}" in html and "Ouvrir la conversation" in html
