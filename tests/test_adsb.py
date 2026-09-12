"""Trafic aérien ADS-B : normalisation, cache, route. Sans réseau."""
import adsb


def _ac(**kw):
    base = {"hex": "c06541", "flight": "ACA1005 ", "r": "C-GMIY", "t": "B38M", "alt_baro": 12850,
            "gs": 351.1, "track": 58.39, "baro_rate": -1088, "squawk": "5726", "emergency": "none",
            "category": "A3", "lat": 45.205627, "lon": -74.416351, "seen": 0.1}
    base.update(kw)
    return base


def test_normalisation_unites_et_categorie():
    a = adsb.normalize(_ac())
    assert a["callsign"] == "ACA1005" and a["reg"] == "C-GMIY" and a["type"] == "B38M"
    assert a["category"] == "large"
    assert a["alt_ft"] == 12850 and a["alt_m"] == 3917          # pieds -> metres
    assert a["speed_kt"] == 351 and a["speed_kmh"] == 650       # noeuds -> km/h
    assert a["vrate_ms"] == -5.5 and a["track"] == 58.4
    assert a["low"] is False and a["emergency"] is None


def test_basse_altitude_et_sol():
    assert adsb.normalize(_ac(alt_baro=300))["low"] is True          # 91 m : concerne un drone
    assert adsb.normalize(_ac(alt_baro=4921))["low"] is True         # 1 500 m pile
    assert adsb.normalize(_ac(alt_baro=4922))["low"] is False
    sol = adsb.normalize(_ac(alt_baro="ground"))
    assert sol["on_ground"] and sol["low"] and sol["alt_m"] is None


def test_urgences():
    assert adsb.normalize(_ac(squawk="7700"))["emergency"] == "emergency"
    assert adsb.normalize(_ac(squawk="7500"))["emergency"] == "hijack"
    assert adsb.normalize(_ac(emergency="general"))["emergency"] == "general"


def test_sans_position_ignore():
    assert adsb.normalize({"hex": "x", "flight": "Y"}) is None


def test_nearby_trie_par_altitude_et_met_en_cache(monkeypatch):
    appels = []
    def faux(url):
        appels.append(url)
        return {"ac": [_ac(alt_baro=12850), _ac(hex="a", flight="LOW", alt_baro=800),
                       _ac(hex="b", flight="GND", alt_baro="ground"), {"hex": "nopos"}]}
    monkeypatch.setattr(adsb, "_get_json", faux)
    adsb._cache.clear()
    d = adsb.nearby(45.5017, -73.5673, 50)
    assert d["count"] == 3 and d["low_count"] == 2
    # les plus bas d'abord (ce sont ceux qui comptent pour un drone), sol/inconnu en dernier
    assert [a["callsign"] for a in d["aircraft"]] == ["LOW", "ACA1005", "GND"]
    assert "/dist/50" in appels[0]
    adsb.nearby(45.53, -73.61, 50)                                # meme zone au dixieme : cache
    assert len(appels) == 1
    adsb.nearby(45.5, -73.6, 999)                                 # rayon borne a 150
    assert appels[-1].endswith("/dist/150")


def test_nearby_source_muette_ou_coordonnees_absurdes(monkeypatch):
    monkeypatch.setattr(adsb, "_get_json", lambda url: None)
    adsb._cache.clear()
    assert adsb.nearby(45.5, -73.6) is None
    assert adsb.nearby(200, 0) is None
    assert adsb.nearby("x", "y") is None


def test_api_adsb(client, monkeypatch):
    monkeypatch.setattr(adsb, "_get_json", lambda url: {"ac": [_ac()]})
    adsb._cache.clear()
    assert client.get("/api/adsb").status_code == 400
    r = client.get("/api/adsb?lat=45.5&lng=-73.6&radius_nm=40")
    assert r.status_code == 200
    j = r.get_json()
    assert j["count"] == 1 and j["aircraft"][0]["callsign"] == "ACA1005" and j["source"] == "adsb.lol"
    monkeypatch.setattr(adsb, "_get_json", lambda url: None)
    adsb._cache.clear()
    assert client.get("/api/adsb?lat=1&lng=1").status_code == 204


def test_carte_embarque_le_bouton_aeronefs(client):
    html = client.get("/pilotes").data.decode()
    assert '"adsb":' in html and "/api/adsb" in html and "'plane-'" in html


def test_helicoptere_reconnu_sans_categorie():
    # A7 emis : helicoptere ; categorie absente mais type ICAO connu : helicoptere aussi
    assert adsb.normalize(_ac(category="A7", t="H60"))["category"] == "rotorcraft"
    assert adsb.normalize(_ac(category="?", t="R44"))["category"] == "rotorcraft"
    assert adsb.normalize(_ac(category="?", t="ec35"))["category"] == "rotorcraft"
    assert adsb.normalize(_ac(category="?", t="C172"))["category"] == "unknown"
    # une categorie emise l'emporte sur le type
    assert adsb.normalize(_ac(category="A1", t="R44"))["category"] == "light"


def test_carte_a_des_symboles_helicoptere(client):
    html = client.get("/pilotes").data.decode()
    assert "heli-" in html and "rotorcraft" in html
