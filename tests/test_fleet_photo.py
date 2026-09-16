"""Flotte : photo d'un appareil (à l'ajout ou après coup), affichée sur la
fiche publique ; fiche technique longue rendue en liste."""
from io import BytesIO

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def test_photo_a_l_ajout_puis_remplacee(make_user, auth_client, app_ctx, client):
    import db
    u = make_user("flotte_photo", role="pilot")
    c = auth_client(u["id"])
    r = c.post("/espace/pilote/drone", data={
        "category": "inspection", "brand": "DJI", "model": "Matrice 4E", "weight_g": "1430",
        "notes": "Masse maximale au décollage : 10,5 kg ; Capteur CMOS 4/3 de 20 MP ; Plage thermique -20 à +50 °C ; "
                 "Évitement d'obstacles omnidirectionnel ; Récepteur ADS-B intégré ; Autonomie 45 min ; Vent 12 m/s ; IP55",
        "photo": (BytesIO(PNG), "m4e.png"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    d = db.fetchone("SELECT * FROM pilot_drones WHERE pilot_user_id=?", (u["id"],))
    assert d["photo_path"] and d["photo_path"].startswith("uploads/u") and "_drone_" in d["photo_path"]
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert 'class="fleet-photo"' in html and "/media/" + d["photo_path"][8:] in html
    # Fiche technique longue : une liste, une ligne par point-virgule
    assert 'class="fleet-spec-list"' in html and "<li>Capteur CMOS 4/3 de 20 MP</li>" in html
    # Remplacement après coup, depuis l'espace pilote
    old = d["photo_path"]
    r = c.post(f"/espace/pilote/drone/{d['id']}/photo", data={"photo": (BytesIO(PNG), "nouvelle.jpg")},
               content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    d2 = db.fetchone("SELECT photo_path FROM pilot_drones WHERE id=?", (d["id"],))
    assert d2["photo_path"] != old and d2["photo_path"].endswith(".jpg")
    # Un autre pilote ne peut pas toucher à cette photo ; un PDF est refusé
    v = make_user("flotte_autre", role="pilot")
    assert auth_client(v["id"]).post(f"/espace/pilote/drone/{d['id']}/photo",
                                     data={"photo": (BytesIO(PNG), "x.png")},
                                     content_type="multipart/form-data").status_code == 403
    r = c.post(f"/espace/pilote/drone/{d['id']}/photo", data={"photo": (BytesIO(b"%PDF-1.4"), "doc.pdf")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "Choisissez une image" in r.data.decode()


def test_carte_sans_photo_reste_propre(make_user, auth_client, app_ctx, client):
    u = make_user("flotte_sans", role="pilot")
    auth_client(u["id"]).post("/espace/pilote/drone", data={"category": "loisir", "brand": "DJI", "model": "Avata 2"},
                              content_type="multipart/form-data")
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "fleet-photo-empty" in html and "Loisir / FPV" in html
