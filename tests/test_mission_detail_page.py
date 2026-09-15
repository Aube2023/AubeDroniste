"""Page /missions/<id> : elle doit s'afficher pour le client (avec devis et
discussion) comme pour le pilote qui a un devis, y compris accepte.

Regression : un `{% set t = threads... %}` dans le gabarit ecrasait la
fonction de traduction `t()` pour tout le reste de la page, et l'inclusion
de _share.html plantait (« 'list' object is not callable ») des qu'un
devis existait : 500 pour le pilote sur sa mission acceptee en prod.
"""


def test_client_et_pilote_voient_la_mission_avec_devis(client, auth_client, open_mission,
                                                       pending_bid, client_user, pilot_user):
    import services
    # Un message dans le fil, pour que la discussion ait du contenu
    services.send_message(mission_id=open_mission, sender_user_id=pilot_user["id"],
                          recipient_user_id=client_user["id"], body="Bonjour, dispo mardi ?")

    html = auth_client(pilot_user["id"]).get(f"/missions/{open_mission}").data.decode()
    assert "Discussion avec le client" in html and "Partager" in html

    html = auth_client(client_user["id"]).get(f"/missions/{open_mission}").data.decode()
    assert "Discussion ·" in html and "Partager" in html

    # Devis accepte : le pilote voit toujours sa mission (cas du 500 en prod)
    services.accept_bid(open_mission, pending_bid, client_user["id"])
    r = auth_client(pilot_user["id"]).get(f"/missions/{open_mission}")
    assert r.status_code == 200 and "Partager" in r.data.decode()
