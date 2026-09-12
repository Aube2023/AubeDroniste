"""Météo sur la carte : relais Open-Meteo + radar, verdict de vol, cache.

Aucun appel réseau : `meteo._get_json` est remplacé. Le verdict est une aide
au pilote, ses seuils sont des règles métier qu'on fige ici.
"""
import meteo


def _cur(**kw):
    base = {"temperature_2m": 18, "precipitation": 0, "weather_code": 1,
            "wind_speed_10m": 10, "wind_gusts_10m": 15, "visibility": 20000, "time": "2026-09-12T15:00"}
    base.update(kw)
    return base


def test_verdict_de_vol_seuils():
    assert meteo.assess(_cur())["level"] == "favorable"
    assert meteo.assess(_cur(wind_speed_10m=30))["level"] == "caution"
    assert meteo.assess(_cur(wind_gusts_10m=42))["level"] == "caution"
    assert meteo.assess(_cur(wind_speed_10m=40))["level"] == "nogo"
    assert meteo.assess(_cur(wind_gusts_10m=60))["level"] == "nogo"
    assert meteo.assess(_cur(precipitation=0.2))["level"] == "caution"
    assert meteo.assess(_cur(precipitation=1.0))["level"] == "nogo"
    assert meteo.assess(_cur(weather_code=95))["reasons"] == ["storm"]
    assert meteo.assess(_cur(weather_code=71))["level"] == "nogo"
    assert meteo.assess(_cur(weather_code=45))["reasons"] == ["visibility"]
    assert meteo.assess(_cur(visibility=1500))["level"] == "caution"
    assert meteo.assess(_cur(temperature_2m=-12))["reasons"] == ["cold"]
    # les raisons bloquantes passent avant les prudences
    r = meteo.assess(_cur(wind_speed_10m=45, temperature_2m=-15))
    assert r["level"] == "nogo" and r["reasons"] == ["wind", "cold"]
    assert meteo.assess({})["level"] == "favorable"          # donnees absentes : pas d'alarme


def test_codes_wmo():
    assert meteo.condition_key(0) == "clear" and meteo.condition_key(3) == "cloudy"
    assert meteo.condition_key(63) == "rain" and meteo.condition_key(99) == "storm"
    assert meteo.condition_key("x") == "unknown" and meteo.condition_key(None) == "unknown"


def test_current_relaie_arrondit_et_met_en_cache(monkeypatch):
    appels = []
    def faux(url):
        appels.append(url)
        return {"current": _cur(wind_speed_10m=27)}
    monkeypatch.setattr(meteo, "_get_json", faux)
    meteo._cache.clear()
    d = meteo.current(45.50171, -73.56729)
    assert d["lat"] == 45.5 and d["lng"] == -73.57          # centieme de degre
    assert d["wind"] == 27 and d["flight"]["level"] == "caution"
    assert d["visibility_km"] == 20.0 and d["condition"] == "partly"
    assert d["source"] == "Open-Meteo"
    meteo.current(45.504, -73.571)                          # meme zone : cache
    assert len(appels) == 1
    assert "latitude=45.5&" in appels[0] and "wind_speed_unit=kmh" in appels[0]


def test_current_source_muette(monkeypatch):
    monkeypatch.setattr(meteo, "_get_json", lambda url: None)
    meteo._cache.clear()
    assert meteo.current(48.85, 2.35) is None
    assert meteo.current(999, 0) is None                    # coordonnees absurdes


def test_radar(monkeypatch):
    monkeypatch.setattr(meteo, "_get_json", lambda url: {
        "host": "https://tilecache.rainviewer.com",
        "radar": {"past": [{"time": i, "path": f"/v2/radar/{i}"} for i in range(10)]}})
    meteo._cache.clear()
    r = meteo.radar()
    assert r["host"].startswith("https://") and len(r["frames"]) == 6
    assert r["latest"]["path"] == "/v2/radar/9"


def test_api_meteo(client, monkeypatch):
    monkeypatch.setattr(meteo, "_get_json", lambda url: {"current": _cur()})
    meteo._cache.clear()
    assert client.get("/api/meteo").status_code == 400
    r = client.get("/api/meteo?lat=45.5&lng=-73.6")
    assert r.status_code == 200 and r.get_json()["flight"]["level"] == "favorable"
    assert "max-age" in r.headers.get("Cache-Control", "")
    monkeypatch.setattr(meteo, "_get_json", lambda url: None)
    meteo._cache.clear()
    assert client.get("/api/meteo?lat=1&lng=1").status_code == 204


def test_api_radar(client, monkeypatch):
    monkeypatch.setattr(meteo, "_get_json", lambda url: {
        "host": "https://tilecache.rainviewer.com", "radar": {"past": [{"time": 1, "path": "/v2/radar/a"}]}})
    meteo._cache.clear()
    r = client.get("/api/meteo/radar")
    assert r.status_code == 200 and r.get_json()["latest"]["path"] == "/v2/radar/a"


def test_carte_embarque_le_bouton_et_les_libelles(client):
    html = client.get("/pilotes").data.decode()
    assert '"meteo":' in html and "Conditions de vol" in html
    assert "/api/meteo/radar" in html
    # la CSP laisse passer les tuiles radar, que MapLibre charge par fetch
    assert "tilecache.rainviewer.com" in client.get("/pilotes").headers.get("Content-Security-Policy", "")
    import json
    # les libelles partent au JS via |tojson, qui echappe les non-ASCII
    assert json.dumps("موسم") in client.get("/ur/pilotes").data.decode()   # traduit comme le reste
