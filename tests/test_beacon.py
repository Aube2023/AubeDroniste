"""AubeBeacon : balises, ingestion de télémétrie, sessions de vol, carte.

Sans réseau : le hub temps réel est absent (BEACON_HUB_SECRET vide en test),
`realtime.publish` est donc un no-op ; la signature des tickets est testée
avec un secret fixe et un vecteur partagé avec le hub (AubeBeacon/docs/api).
"""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import db
import services
from beacon import devices, flights, realtime, status, telemetry
from beacon.schema import TABLE_NAMES

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,}$")


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _packet(uid, pid, *, lat=45.5017, lng=-73.5673, state="READY", ts=None, alt_rel=0.0,
            speed=0.0, heading=0.0, position=True, **extra):
    p = {
        "version": 1, "device_id": uid, "packet_id": pid, "timestamp": ts,
        "position": {"latitude": lat, "longitude": lng, "gnss_altitude_m": 82.4,
                     "barometric_altitude_m": 81.9, "relative_altitude_m": alt_rel,
                     "speed_mps": speed, "heading_deg": heading} if position else None,
        "gnss": {"fix": position, "satellites": 17},
        "network": {"type": "LTE-M", "signal_dbm": -87},
        "device": {"battery_percent": 83, "firmware": "0.1.0", "uptime_s": 12},
        "flight": {"state": state},
    }
    p.update(extra)
    return p


def _post(client, token, body):
    return client.post("/api/v1/telemetry", data=json.dumps(body), content_type="application/json",
                       headers={"Authorization": f"Bearer {token}"} if token else {})


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def pilot_with_drone(make_user):
    u = make_user("bcn_pilot", role="both", lat=45.5, lng=-73.6)
    drone_id = services.add_drone(u["id"], category="pro_camera", brand="DJI", model="Mavic 3")
    return u, drone_id


@pytest.fixture()
def beacon(pilot_with_drone):
    u, drone_id = pilot_with_drone
    device, token = devices.create_device(u["id"], label="Balise de test", drone_id=drone_id)
    return u, drone_id, device, token


# ---------------------------------------------------------------------------
# Schéma et identité
# ---------------------------------------------------------------------------

def test_schema_sql_a_jour():
    sql = open("schema.sql", encoding="utf-8").read()
    for name in TABLE_NAMES:
        assert f"CREATE TABLE IF NOT EXISTS {name}" in sql


def test_creation_balise_jeton_hache(app_ctx, pilot_with_drone):
    u, drone_id = pilot_with_drone
    device, token = devices.create_device(u["id"], label="Test", drone_id=drone_id)
    assert re.match(r"^AUBE-BCN-\d{6}$", device["device_uid"])
    assert TOKEN_RE.match(token)
    row = db.fetchone("SELECT token_hash FROM beacon_devices WHERE id=?", (device["id"],))
    assert row["token_hash"] != token and token not in row["token_hash"]
    assert devices.authenticate(device["device_uid"], token)[0]["id"] == device["id"]
    assert devices.authenticate(device["device_uid"], "mauvais")[1] == "bad_token"
    assert devices.authenticate("AUBE-BCN-999999", token)[1] == "unknown"
    devices.set_enabled(device["id"], u["id"], False)
    assert devices.authenticate(device["device_uid"], token)[1] == "disabled"
    kinds = [e["kind"] for e in devices.list_events(device["id"])]
    assert "created" in kinds and "auth_failed" in kinds and "disabled" in kinds


def test_identifiant_personnalise_et_doublon(app_ctx, pilot_with_drone):
    u, _ = pilot_with_drone
    d, _ = devices.create_device(u["id"], device_uid="sim777")
    assert d["device_uid"] == "AUBE-BCN-SIM777"
    with pytest.raises(devices.DeviceError):
        devices.create_device(u["id"], device_uid="AUBE-BCN-SIM777")
    with pytest.raises(devices.DeviceError):
        devices.create_device(u["id"], device_uid="AUBE-BCN-x")


def test_drone_etranger_refuse(app_ctx, pilot_with_drone, make_user):
    u, drone_id = pilot_with_drone
    other = make_user("bcn_other", role="both")
    with pytest.raises(devices.DeviceError):
        devices.create_device(other["id"], drone_id=drone_id)


def test_rotation_du_jeton(app_ctx, beacon):
    u, _, device, token = beacon
    new = devices.rotate_token(device["id"], u["id"])
    assert new != token
    assert devices.authenticate(device["device_uid"], token)[1] == "bad_token"
    assert devices.authenticate(device["device_uid"], new)[0]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validation_stricte():
    ok = telemetry.parse_packet(_packet("AUBE-BCN-000001", "a-1", ts="2026-09-21T08:00:00Z", extra_key=1))
    assert ok["timestamp"] == "2026-09-21T08:00:00.000Z" and ok["state"] == "READY"
    with pytest.raises(telemetry.TelemetryError) as e:
        telemetry.parse_packet(_packet("AUBE-BCN-000001", "a-1", lat=95))
    assert e.value.path == "position.latitude"
    with pytest.raises(telemetry.TelemetryError):
        telemetry.parse_packet({**_packet("AUBE-BCN-000001", "a-1"), "version": 2})
    with pytest.raises(telemetry.TelemetryError):
        telemetry.parse_packet(_packet("AUBE-BCN-000001", "a-1", state="HOVER"))
    with pytest.raises(telemetry.TelemetryError):
        telemetry.parse_packet(_packet("AUBE-BCN-000001", "bad id"))
    with pytest.raises(telemetry.TelemetryError):
        telemetry.parse_packet(_packet("AUBE-BCN-000001", "a-1", speed=500))
    with pytest.raises(telemetry.TelemetryError):
        telemetry.parse_packet(_packet("AUBE-BCN-000001", "a-1", ts="2999-01-01T00:00:00Z"))
    hb = telemetry.parse_packet(_packet("AUBE-BCN-000001", "a-2", position=False, state="GPS_LOST"))
    assert hb["position"] is None and hb["gnss"]["fix"] is False


# ---------------------------------------------------------------------------
# API d'ingestion
# ---------------------------------------------------------------------------

def test_ingestion_auth_et_formats(client, beacon):
    u, _, device, token = beacon
    uid = device["device_uid"]
    assert _post(client, None, _packet(uid, "p1")).status_code == 401
    assert _post(client, "faux", _packet(uid, "p1")).status_code == 401
    r = client.post("/api/v1/telemetry", data="x", content_type="text/plain",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 415
    r = client.post("/api/v1/telemetry", data="{not json", content_type="application/json",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_json"
    r = _post(client, token, _packet(uid, "p1", lat=200))
    assert r.status_code == 400 and r.get_json()["path"] == "position.latitude"
    big = _packet(uid, "p1", pad="x" * 20000)
    assert _post(client, token, big).status_code == 413
    r = _post(client, token, _packet(uid, "p1"))
    assert r.status_code == 200
    j = r.get_json()
    assert j["success"] and j["telemetry_id"] and j["flight_id"] is None and not j["duplicate"]
    assert j["intervals_ms"]["flying"] == 2000
    # rejeu du même paquet : idempotent
    r2 = _post(client, token, _packet(uid, "p1")).get_json()
    assert r2["duplicate"] and r2["telemetry_id"] == j["telemetry_id"]
    assert db.fetchone("SELECT COUNT(*) AS n FROM beacon_telemetry WHERE device_id=?", (device["id"],))["n"] == 1


def test_balise_desactivee_refusee(client, beacon):
    u, _, device, token = beacon
    devices.set_enabled(device["id"], u["id"], False)
    r = _post(client, token, _packet(device["device_uid"], "p1"))
    assert r.status_code == 403 and r.get_json()["error"] == "device_disabled"


def test_lot_rejoue_et_ordre(client, beacon):
    u, _, device, token = beacon
    uid = device["device_uid"]
    t0 = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
    batch = [_packet(uid, f"b-{i}", lat=45.50 + i * 1e-4, ts=_iso(t0 + timedelta(seconds=2 * i)),
                     state="FLYING", speed=5.0, alt_rel=20.0) for i in range(5)]
    r = _post(client, token, batch)
    assert r.status_code == 200
    j = r.get_json()
    assert j["accepted"] == 5 and j["duplicates"] == 0 and len(j["results"]) == 5
    j2 = _post(client, token, batch).get_json()
    assert j2["accepted"] == 0 and j2["duplicates"] == 5
    assert _post(client, token, batch + [_packet("AUBE-BCN-000009", "x")]).status_code == 400
    assert _post(client, token, []).status_code == 400


def test_battement_de_coeur_sans_position(client, beacon):
    u, _, device, token = beacon
    r = _post(client, token, _packet(device["device_uid"], "hb-1", position=False, state="GPS_LOST"))
    j = r.get_json()
    assert r.status_code == 200 and j["telemetry_id"] is None
    d = devices.get_device(device["id"], u["id"])
    assert d["last_state"] == "GPS_LOST" and d["last_seen_at"] and d["last_battery"] == 83
    assert d["firmware_version"] == "0.1.0"
    assert db.fetchone("SELECT COUNT(*) AS n FROM beacon_telemetry WHERE device_id=?", (device["id"],))["n"] == 0


# ---------------------------------------------------------------------------
# Sessions de vol
# ---------------------------------------------------------------------------

def _fly(client, token, uid, t0, n=10, step_lat=1e-3):
    """Décollage puis n points en vol puis atterrissage. Retourne les réponses."""
    out = [_post(client, token, _packet(uid, "t-0", ts=_iso(t0), state="TAKEOFF", speed=1.0, alt_rel=1.0)).get_json()]
    for i in range(1, n + 1):
        out.append(_post(client, token, _packet(
            uid, f"t-{i}", lat=45.50 + i * step_lat, ts=_iso(t0 + timedelta(seconds=2 * i)),
            state="FLYING", speed=10.0 + i, alt_rel=30.0 + i, heading=90)).get_json())
    out.append(_post(client, token, _packet(uid, "t-land", lat=45.50 + n * step_lat,
                                            ts=_iso(t0 + timedelta(seconds=2 * n + 4)), state="LANDED")).get_json())
    return out


def test_cycle_de_vol_et_statistiques(client, beacon):
    u, drone_id, device, token = beacon
    uid = device["device_uid"]
    t0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    res = _fly(client, token, uid, t0, n=10)
    fid = res[0]["flight_id"]
    assert fid and all(r["flight_id"] == fid for r in res)
    f = flights.get_flight(fid, u["id"])
    assert f["status"] == "FINISHED" and f["drone_id"] == drone_id
    assert f["points"] == 12 and f["duration_s"] == 24
    assert f["max_speed_mps"] == 20.0 and f["max_relative_altitude_m"] == 40.0
    # 10 pas de 0,001° de latitude ≈ 1 111 m
    assert 1050 < f["distance_m"] < 1170
    assert f["takeoff_at"] == "2026-09-21T09:00:00.000Z" and f["landing_at"] == "2026-09-21T09:00:24.000Z"
    assert f["last_lat"] == pytest.approx(45.51)
    assert flights.active_flight(device["id"]) is None
    # trace simplifiée : ligne droite → deux points suffisent
    tr = flights.track(fid)
    assert 2 <= len(tr) <= 12 and tr[0][1] == pytest.approx(45.5017) and tr[-1][1] == pytest.approx(45.51)


def test_faux_depart_abandonne(client, beacon):
    u, _, device, token = beacon
    uid = device["device_uid"]
    t0 = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    a = _post(client, token, _packet(uid, "f-0", ts=_iso(t0), state="TAKEOFF")).get_json()
    b = _post(client, token, _packet(uid, "f-1", ts=_iso(t0 + timedelta(seconds=2)), state="READY")).get_json()
    assert a["flight_id"] == b["flight_id"]
    assert flights.get_flight(a["flight_id"], u["id"])["status"] == "ABORTED"


def test_redemarrage_en_vol_et_silence(client, beacon, monkeypatch):
    u, _, device, token = beacon
    uid = device["device_uid"]
    t0 = datetime(2026, 9, 21, 11, 0, tzinfo=timezone.utc)
    a = _post(client, token, _packet(uid, "r-0", ts=_iso(t0), state="FLYING", speed=8)).get_json()
    assert a["flight_id"] and flights.get_flight(a["flight_id"], u["id"])["status"] == "ACTIVE"
    # silence de 20 minutes puis nouveau décollage : l'ancienne session est abandonnée
    b = _post(client, token, _packet(uid, "r-1", ts=_iso(t0 + timedelta(minutes=20)), state="TAKEOFF")).get_json()
    assert b["flight_id"] != a["flight_id"]
    assert flights.get_flight(a["flight_id"], u["id"])["status"] == "ABORTED"
    assert flights.get_flight(b["flight_id"], u["id"])["status"] == "PREPARING"


def test_saut_impossible_marque(client, beacon):
    u, _, device, token = beacon
    uid = device["device_uid"]
    t0 = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    _post(client, token, _packet(uid, "j-0", ts=_iso(t0), state="FLYING", speed=5))
    j = _post(client, token, _packet(uid, "j-1", lat=46.5, ts=_iso(t0 + timedelta(seconds=2)),
                                     state="FLYING", speed=5)).get_json()
    assert "jump" in j["flags"]
    f = flights.get_flight(j["flight_id"], u["id"])
    assert f["distance_m"] == 0.0 and f["last_lat"] == pytest.approx(45.5017)
    d = devices.get_device(device["id"], u["id"])
    assert d["last_lat"] == pytest.approx(45.5017)      # la position affichée ne saute pas
    assert len(flights.track(f["id"])) == 1


def test_simplification():
    line = [[0.0 + i * 1e-5, 45.0, 0, "t", 0] for i in range(200)]
    assert len(flights.simplify(line)) == 2
    zig = [[i * 1e-4, 45.0 + (1e-4 if i % 2 else 0), 0, "t", 0] for i in range(2000)]
    assert len(flights.simplify(zig, max_points=100)) == 100


# ---------------------------------------------------------------------------
# État de liaison
# ---------------------------------------------------------------------------

def test_statut_de_liaison():
    now = datetime(2026, 9, 21, 12, 0, 30, tzinfo=timezone.utc)
    assert status.compute(None, now) == ("NEVER", None)
    assert status.compute("2026-09-21T12:00:25.000Z", now)[0] == "ONLINE"
    assert status.compute("2026-09-21T12:00:10.000Z", now)[0] == "DEGRADED"
    assert status.compute("2026-09-21T11:59:00.000Z", now)[0] == "OFFLINE"
    assert status.compute("2026-09-21 12:00:28", now)[0] == "ONLINE"     # format SQLite


# ---------------------------------------------------------------------------
# Lecture pour la carte et cloisonnement
# ---------------------------------------------------------------------------

def test_live_cloisonne(client, auth_client, beacon, make_user):
    u, _, device, token = beacon
    _post(client, token, _packet(device["device_uid"], "l-0", state="READY"))
    assert client.get("/api/v1/beacon/live").status_code == 401
    j = auth_client(u["id"]).get("/api/v1/beacon/live").get_json()
    mine = [d for d in j["devices"] if d["id"] == device["id"]]
    assert len(mine) == 1 and mine[0]["status"] == "ONLINE" and mine[0]["position"]["latitude"] == 45.5017
    assert "token" not in json.dumps(j) and j["thresholds"]["online_s"] == 10
    other = make_user("bcn_stranger", role="both")
    j2 = auth_client(other["id"]).get("/api/v1/beacon/live").get_json()
    assert all(d["id"] != device["id"] for d in j2["devices"])
    assert auth_client(other["id"]).get(f"/api/v1/beacon/flights/1/track").status_code in (404, 200)


def test_live_admin_voit_tout(client, auth_client, beacon, make_user):
    u, _, device, token = beacon
    admin = make_user("bcn_admin", role="both")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    c = auth_client(admin["id"])
    assert all(d["id"] != device["id"] for d in c.get("/api/v1/beacon/live").get_json()["devices"])
    assert any(d["id"] == device["id"] for d in c.get("/api/v1/beacon/live?all=1").get_json()["devices"])


def test_ticket_sans_hub(auth_client, beacon):
    u, *_ = beacon
    assert auth_client(u["id"]).get("/api/v1/beacon/ws-ticket").status_code == 503


# ---------------------------------------------------------------------------
# Tickets temps réel (vecteur partagé avec le hub)
# ---------------------------------------------------------------------------

VECTOR_SECRET = "aubebeacon-test-secret"
VECTOR_PAYLOAD = {"exp": 1800000000, "n": "abc123", "r": ["beacon:1", "beacon:2"], "u": 7}


def test_ticket_signature_et_expiration():
    t = realtime.sign_ticket(VECTOR_PAYLOAD, VECTOR_SECRET)
    assert realtime.verify_ticket(t, VECTOR_SECRET, now=1799999999) == VECTOR_PAYLOAD
    assert realtime.verify_ticket(t, VECTOR_SECRET, now=1800000001) is None
    assert realtime.verify_ticket(t, "autre-secret", now=1799999999) is None
    body, sig = t.split(".")
    assert realtime.verify_ticket(body + "x." + sig, VECTOR_SECRET, now=1799999999) is None
    assert realtime.verify_ticket("n'importe quoi", VECTOR_SECRET) is None


def test_ticket_vecteur_partage():
    # Même vecteur dans AubeBeacon/docs/api/ticket-vector.json et le test du hub.
    t = realtime.sign_ticket(VECTOR_PAYLOAD, VECTOR_SECRET)
    assert t == ("eyJleHAiOjE4MDAwMDAwMDAsIm4iOiJhYmMxMjMiLCJyIjpbImJlYWNvbjoxIiwiYmVhY29uOjIiXSwidSI6N30."
                 "FxOQhsaXJDcvU2qHzrmq-QBvNDokI8C3AbA1ruyzD-A")


# ---------------------------------------------------------------------------
# Pages de l'espace pilote
# ---------------------------------------------------------------------------

def test_page_balises_creation_et_jeton_unique(auth_client, pilot_with_drone, client):
    u, drone_id = pilot_with_drone
    c = auth_client(u["id"])
    assert c.get("/espace/pilote/aubebeacon").status_code == 200
    r = c.post("/espace/pilote/aubebeacon/creer", data={"label": "Ma balise", "drone_id": drone_id},
               follow_redirects=True)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "AUBE-BCN-" in html
    tokens = re.findall(r"DEVICE_TOKEN=([A-Za-z0-9_-]{40,})", html)
    assert len(tokens) == 1
    html2 = c.get("/espace/pilote/aubebeacon").get_data(as_text=True)
    assert tokens[0] not in html2                       # affiché une seule fois
    uid = re.search(r"AUBE-BCN-\d{6}", html).group(0)
    d = devices.get_by_uid(uid)
    assert d["label"] == "Ma balise" and d["drone_id"] == drone_id
    # le jeton fonctionne
    assert _post(client, tokens[0], _packet(uid, "w-1")).status_code == 200
    # rotation : nouveau jeton affiché, ancien mort
    r = c.post(f"/espace/pilote/aubebeacon/{d['id']}/jeton", follow_redirects=True)
    new = re.findall(r"DEVICE_TOKEN=([A-Za-z0-9_-]{40,})", r.get_data(as_text=True))
    assert len(new) == 1 and new[0] != tokens[0]
    assert _post(client, tokens[0], _packet(uid, "w-2")).status_code == 401
    assert _post(client, new[0], _packet(uid, "w-2")).status_code == 200
    # désactivation, dissociation, suppression
    c.post(f"/espace/pilote/aubebeacon/{d['id']}/desactiver", follow_redirects=True)
    assert _post(client, new[0], _packet(uid, "w-3")).status_code == 403
    c.post(f"/espace/pilote/aubebeacon/{d['id']}/activer", follow_redirects=True)
    c.post(f"/espace/pilote/aubebeacon/{d['id']}/dissocier", follow_redirects=True)
    assert devices.get_by_uid(uid)["drone_id"] is None
    c.post(f"/espace/pilote/aubebeacon/{d['id']}/supprimer", follow_redirects=True)
    assert devices.get_by_uid(uid) is None


def test_page_balises_refusee_au_client_et_a_l_etranger(auth_client, make_user, beacon):
    u, _, device, _ = beacon
    c = auth_client(make_user("bcn_client", role="client")["id"])
    assert c.get("/espace/pilote/aubebeacon").status_code == 403
    other = auth_client(make_user("bcn_pilot2", role="both")["id"])
    assert other.post(f"/espace/pilote/aubebeacon/{device['id']}/jeton").status_code == 404
    assert other.post(f"/espace/pilote/aubebeacon/{device['id']}/supprimer").status_code == 404
    assert devices.get_by_uid(device["device_uid"]) is not None


def test_page_vol(auth_client, client, beacon):
    u, _, device, token = beacon
    t0 = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)
    res = _fly(client, token, device["device_uid"], t0, n=5)
    fid = res[0]["flight_id"]
    c = auth_client(u["id"])
    html = c.get(f"/espace/pilote/aubebeacon/vols/{fid}").get_data(as_text=True)
    assert "FINISHED" in html or "Termin" in html
    j = c.get(f"/api/v1/beacon/flights/{fid}/track").get_json()
    assert j["flight"]["points"] == 7 and len(j["track"]) >= 2
