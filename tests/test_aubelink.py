"""AubeLink vu d'AubePilot : client, route JSON, actions, panneau, pannes.

Aucun appel sortant : `beacon.aubelink._request`, seule fonction réseau du
client, est remplacée par FakeAubeLink, qui joue AubeLink (beacon-status,
PUT et DELETE /api/v1/drones/{droneId}/beacon, propriété stricte par
X-Acting-User) ou rend des réponses imposées (`script`). Un seul test parle
à un vrai serveur HTTP, local (127.0.0.1), pour vérifier `_request` lui-même.
Les réponses suivent les types d'AubeLink (packages/types/src/api.ts :
BeaconStatusResponse, DroneSummary, EventItem, MessageItem) et ses erreurs
{"error": {"code", "message", "details?"}}.
"""
import http.server
import importlib.util
import json
import logging
import os
import pathlib
import re
import sys
import threading
import time

import markupsafe
import pytest

import config
import db
import i18n
import security
import services
from beacon import aubelink, devices, views
from beacon.aubelink import Resp
from tests.test_beacon import _grant

AUBEBEACON_JS = re.compile(r"window\.AUBEBEACON = (\{.*?\});\n</script>", re.S)

KEY = "alp_test_x"
PAGE = "/espace/pilote/aubebeacon"
ROUTE = "/api/v1/beacon/aubelink"
STATUS = "/api/v1/beacon-status"
PUBLIC = "https://link.aubeetoilee.com"


def _err(status, code, details=None):
    body = {"error": {"code": code, "message": code.lower().replace("_", " ")}}
    if details is not None:
        body["error"]["details"] = details
    return Resp(status, body, None)


class FakeAubeLink:
    """Faux AubeLink. `drones[acting][droneId]` = DroneSummary ; un drone ne
    se voit et ne se modifie que par son propriétaire (X-Acting-User)."""

    def __init__(self):
        self.calls = []
        self.drones = {}
        self.script = []
        self.alerts = {}       # droneId -> EventItem[] imposés (sinon une alerte LOW_BATTERY)
        self.messages = {}     # droneId -> MessageItem[] imposés (sinon une commande STATUS REQUEST)

    def add(self, acting, drone_id, name="Drone", beacon_id=None, **extra):
        drone = {
            "id": f"uuid-{drone_id.lower()}", "droneId": drone_id, "name": name, "model": None,
            "ownerId": f"owner-uuid-{acting}", "status": "ONLINE", "flightState": "FLYING",
            "linkState": "CONNECTED", "gnssState": "OK", "flightMode": "AUTO",
            "lastSeen": "2026-09-25T14:03:10.000Z",
            "telemetry": {"timestamp": "2026-09-25T14:03:10.000Z", "latitude": 45.5017, "longitude": -73.5673,
                          "altitudeM": 40.0, "speedKmh": 30.0, "headingDeg": 90.0, "batteryPercent": 78,
                          "voltage": 15.2, "satellites": 14, "late": False},
            "activeFlightId": "flight-uuid-1", "activeAlerts": 1, "beaconId": beacon_id, "enabled": True,
        }
        drone.update(extra)
        self.drones.setdefault(acting, {})[drone_id] = drone
        return drone

    @staticmethod
    def _alert(drone_id):
        return {"id": "evt-1", "droneId": drone_id, "flightId": "flight-uuid-1", "category": "ALERT",
                "code": "LOW_BATTERY", "severity": "WARNING", "source": "DRONE", "message": "Batterie faible",
                "data": {"latitude": 45.5, "longitude": -73.5}, "timestamp": "2026-09-25T14:02:00.000Z",
                "acknowledgedAt": None, "clearedAt": None}

    @staticmethod
    def _message(drone_id):
        return {"id": "msg-1", "messageId": "m-1", "droneId": drone_id, "flightId": "flight-uuid-1",
                "direction": "UPLINK", "kind": "command", "sender": "xros@aubemail.com", "text": None,
                "category": None, "command": "STATUS REQUEST", "fields": None, "status": "ACKNOWLEDGED",
                "requiresAck": True, "attempts": 1, "referenceMessageId": None, "nack": None,
                "createdAt": "2026-09-25T14:01:00.000Z", "sentAt": None, "acknowledgedAt": None}

    def __call__(self, method, path, acting, body=None, **kwargs):
        self.calls.append((method, path, acting, body))
        if self.script:
            return self.script.pop(0)
        mine = self.drones.get(acting, {})
        if method == "GET" and path == STATUS:
            items = []
            for drone_id in sorted(mine):
                drone = mine[drone_id]
                linked = bool(drone["beaconId"])
                items.append({"drone": drone,
                              "alerts": self.alerts.get(drone_id, [self._alert(drone_id)]) if linked else [],
                              "lastMessages": self.messages.get(drone_id, [self._message(drone_id)]) if linked else []})
            return Resp(200, {"serverTime": "2026-09-25T14:03:11.000Z", "drones": items, "truncated": False}, None)
        match = re.fullmatch(r"/api/v1/drones/([^/]+)/beacon", path)
        if match and method == "PUT":
            drone = mine.get(match.group(1))
            if not drone:
                return _err(404, "NOT_FOUND")
            uid = body["beaconId"]
            for owner, drones in self.drones.items():
                for other in drones.values():
                    if other["beaconId"] == uid and other is not drone:
                        return _err(409, "BEACON_ALREADY_LINKED",
                                    {"droneId": other["droneId"]} if owner == acting else None)
            drone["beaconId"] = uid
            return Resp(200, {"beaconId": uid, "droneId": drone["droneId"], "droneName": drone["name"],
                              "source": "AUBEPILOT", "linkedAt": "2026-09-25T14:03:11.000Z"}, None)
        if match and method == "DELETE":
            drone = mine.get(match.group(1))
            if not drone or not drone["beaconId"]:
                return _err(404, "NOT_FOUND")
            drone["beaconId"] = None
            return Resp(204, None, None)
        return _err(404, "NOT_FOUND")

    def methods_since(self, n):
        return [c[0] for c in self.calls[n:]]


@pytest.fixture()
def pilot_with_drone(make_user):
    """Comme tests/test_beacon.py::pilot_with_drone, mais sans coordonnées : la
    base de test est partagée, et des pilotes géolocalisés en nombre repoussent
    ceux des tests de recherche « autour de » (test_discovery) au-delà de la
    limite de 50 résultats de /api/pilotes."""
    u = _grant(make_user("al_pilot", role="both"))
    drone_id = services.add_drone(u["id"], category="pro_camera", brand="DJI", model="Mavic 3")
    return u, drone_id


@pytest.fixture()
def beacon(pilot_with_drone):
    """Même forme que tests/test_beacon.py::beacon : (pilote, drone, balise, jeton)."""
    u, drone_id = pilot_with_drone
    device, token = devices.create_device(u["id"], label="Balise de test", drone_id=drone_id)
    return u, drone_id, device, token


@pytest.fixture(autouse=True)
def _cache_vide():
    aubelink.clear_cache()
    yield
    aubelink.clear_cache()


@pytest.fixture()
def fake(monkeypatch):
    f = FakeAubeLink()
    monkeypatch.setattr(aubelink, "_request", f)
    monkeypatch.setattr(config, "AUBELINK_URL", "http://aubelink.test")
    monkeypatch.setattr(config, "AUBELINK_KEY", KEY)
    monkeypatch.setattr(config, "AUBELINK_PUBLIC_URL", PUBLIC)
    return f


@pytest.fixture()
def clock(monkeypatch):
    now = [10_000.0]
    monkeypatch.setattr(aubelink, "_now", lambda: now[0])
    return now


def _acting(user):
    return f"{user['username']}@aubemail.com"


def _flash(key, **kwargs):
    return str(markupsafe.escape(i18n.t(key, "fr", **kwargs)))


def _link(c, device_id, drone_id, suffix=""):
    return c.post(f"{PAGE}/{device_id}/aubelink{suffix}", data={"aubelink_drone_id": drone_id},
                  follow_redirects=True)


def _unlink(c, device_id, suffix=""):
    return c.post(f"{PAGE}/{device_id}/aubelink/retirer{suffix}", follow_redirects=True)


# ---------------------------------------------------------------------------
# 1. Fonction non configurée : inerte
# ---------------------------------------------------------------------------

def test_non_configure_aucun_appel(auth_client, beacon, monkeypatch):
    f = FakeAubeLink()
    monkeypatch.setattr(aubelink, "_request", f)
    monkeypatch.setattr(config, "AUBELINK_URL", "")
    monkeypatch.setattr(config, "AUBELINK_KEY", "")
    u, _, device, _ = beacon
    c = auth_client(u["id"])
    r = c.get(PAGE)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "bcn-link" not in html and "data-aubelink-for" not in html and "aubelink_drone_id" not in html
    r = c.get(ROUTE)
    assert r.status_code == 503 and r.get_json()["error"] == "aubelink_not_configured"
    assert c.post(f"{PAGE}/{device['id']}/aubelink", data={"aubelink_drone_id": "AE-XR-001"}).status_code == 404
    assert c.post(f"{PAGE}/{device['id']}/aubelink/retirer").status_code == 404
    # une clé qui n'est pas une clé d'intégration AubeLink laisse la fonction inerte
    monkeypatch.setattr(config, "AUBELINK_URL", "http://aubelink.test")
    monkeypatch.setattr(config, "AUBELINK_KEY", "pas-une-cle")
    assert c.get(ROUTE).status_code == 503
    # la suppression d'une balise n'appelle pas AubeLink
    c.post(f"{PAGE}/{device['id']}/supprimer", follow_redirects=True)
    assert devices.get_by_uid(device["device_uid"]) is None
    assert f.calls == []


# ---------------------------------------------------------------------------
# 2. Page, route JSON et identité déléguée
# ---------------------------------------------------------------------------

def test_page_et_route_liste_blanche(auth_client, beacon, fake):
    u, _, device, _ = beacon
    acting = _acting(u)
    uid = device["device_uid"]
    free, _ = devices.create_device(u["id"], label="Balise libre")
    fake.add(acting, "AE-XR-001", name="Mavic XR", beacon_id=uid)
    fake.add(acting, "AE-XR-002", name="Second")
    c = auth_client(u["id"])

    r = c.get(PAGE)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert f'data-aubelink-for="{device["id"]}"' in html and f'data-aubelink-for="{free["id"]}"' in html
    assert "Mavic XR · AE-XR-001" in html and ">CONNECTED<" in html and "LOW_BATTERY" in html
    assert "STATUS REQUEST" in html and "78 %" in html
    assert f"{PUBLIC}/app/drones/AE-XR-001" in html and f"{PUBLIC}/app/vols/flight-uuid-1" in html
    assert 'rel="noopener noreferrer"' in html
    # sélecteur de la balise libre : le drone qui porte déjà une balise est désactivé
    assert '<option value="AE-XR-001" disabled>' in html and '<option value="AE-XR-002">' in html
    assert _flash("aubelink.carries", uid=uid) in html
    assert _flash("aubelink.not_linked") in html
    assert "alp_" not in html
    # état rendu par le serveur (les boutons en dépendent) et libellés du sondage
    assert f'data-aubelink-for="{device["id"]}" data-al-rendered="true"' in html
    assert f'data-aubelink-for="{free["id"]}" data-al-rendered="false"' in html
    labels = _page_config(html)["l10n"]["aubelink"]
    assert labels["reload"] == i18n.t("aubelink.reload", "fr")
    assert labels["ownerDeleted"] == i18n.t("aubelink.owner_deleted", "fr")

    r = c.get(ROUTE)
    raw = r.get_data(as_text=True)
    j = r.get_json()
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert j["available"] is True and j["reason"] is None
    view = j["beacons"][str(device["id"])]
    assert view["uid"] == uid and view["linked"] is True
    assert view["drone"] == {
        "droneId": "AE-XR-001", "name": "Mavic XR", "status": "ONLINE", "enabled": True,
        "linkState": "CONNECTED", "flightState": "FLYING", "gnssState": "OK",
        "lastSeen": "2026-09-25T14:03:10.000Z", "batteryPercent": 78, "activeAlerts": 1,
        "activeFlightId": "flight-uuid-1",
    }
    assert view["alerts"] == [{"code": "LOW_BATTERY", "severity": "WARNING", "message": "Batterie faible",
                               "timestamp": "2026-09-25T14:02:00.000Z"}]
    assert view["messages"][0]["command"] == "STATUS REQUEST" and view["messages"][0]["direction"] == "UPLINK"
    assert view["urls"] == {"drone": f"{PUBLIC}/app/drones/AE-XR-001", "flight": f"{PUBLIC}/app/vols/flight-uuid-1"}
    assert j["beacons"][str(free["id"])] == {"uid": free["device_uid"], "linked": False}
    assert j["drones"][str(u["id"])] == [
        {"droneId": "AE-XR-001", "name": "Mavic XR", "beaconId": uid},
        {"droneId": "AE-XR-002", "name": "Second", "beaconId": None},
    ]
    # liste blanche : ni clé, ni coordonnées, ni identifiants internes, ni adresse déléguée
    for leak in ("alp_", "latitude", "longitude", "ownerId", "owner-uuid", "uuid-ae-xr", "telemetry", acting):
        assert leak not in raw
    # chaque appel porte l'adresse du propriétaire ; la page puis la route : une seule lecture (cache)
    assert fake.calls and all(call[2] == acting for call in fake.calls)
    assert fake.methods_since(0) == ["GET"]


def test_batterie_absente_et_sans_lien_public(auth_client, beacon, fake, monkeypatch):
    u, _, device, _ = beacon
    monkeypatch.setattr(config, "AUBELINK_PUBLIC_URL", "")
    fake.add(_acting(u), "AE-XR-001", beacon_id=device["device_uid"], telemetry=None, activeFlightId=None)
    c = auth_client(u["id"])
    view = c.get(ROUTE).get_json()["beacons"][str(device["id"])]
    assert view["drone"]["batteryPercent"] is None and view["urls"] is None
    assert "/app/drones/" not in c.get(PAGE).get_data(as_text=True)


def _page_config(html):
    """window.AUBEBEACON tel que la page le pose (JSON décodé)."""
    return json.loads(AUBEBEACON_JS.search(html).group(1))


def test_aucune_coordonnee_dans_les_textes(auth_client, beacon, fake):
    """Un POSITION REQUEST fait répondre le drone par un POSITION_REPORT dont le
    texte porte sa position (AubeLink, apps/node/src/reports.ts) : ni ce texte ni
    une coordonnée écrite en clair ailleurs n'arrivent dans AubePilot."""
    u, _, device, _ = beacon
    fake.add(_acting(u), "AE-XR-001", name="Mavic XR", beacon_id=device["device_uid"])
    base = FakeAubeLink._message("AE-XR-001")
    fake.messages["AE-XR-001"] = [
        base | {"id": "msg-3", "direction": "DOWNLINK", "kind": "message", "sender": "DRONE",
                "category": "POSITION_REPORT", "command": None, "status": "RECEIVED",
                "text": "POSITION 45.50170N 073.56730W\nALT 100 M\nGS 54 KMH\nHDG 270",
                "fields": {"position_known": True, "latitude": 45.5017, "longitude": -73.5673}},
        base | {"id": "msg-2", "direction": "UPLINK", "kind": "message", "category": "FREE_TEXT",
                "command": None, "text": "Rejoindre 45,50170 -73,56730 puis 45°30'06\"N, batterie 72 %"},
        base | {"id": "msg-1", "direction": "UPLINK", "kind": "message", "category": "POSITION_REQUEST",
                "command": None, "text": "POSITION REQUEST"},
    ]
    fake.alerts["AE-XR-001"] = [FakeAubeLink._alert("AE-XR-001") | {
        "code": "GEOFENCE_BREACH", "severity": "CRITICAL", "message": "Sortie de zone à 45.50170, -73.56730"}]
    forbidden = ("45.5017", "45,5017", "73.5673", "73,5673", "45°30")
    c = auth_client(u["id"])

    html = c.get(PAGE).get_data(as_text=True)
    page_json = json.dumps(_page_config(html)["aubelink"], ensure_ascii=False)
    route = c.get(ROUTE).get_json()
    route_json = json.dumps(route, ensure_ascii=False)
    admin_json = json.dumps(c.get(ROUTE + "?all=1").get_json(), ensure_ascii=False)
    for text in (html, page_json, route_json, admin_json):
        for leak in forbidden:
            assert leak not in text, leak
    for text in (page_json, route_json, admin_json):
        assert "latitude" not in text and "longitude" not in text and "fields" not in text

    messages = route["beacons"][str(device["id"])]["messages"]
    # compte rendu de position : catégorie seule, affichée à la place du texte
    assert messages[0]["text"] is None and messages[0]["category"] == "POSITION_REPORT"
    assert "DOWNLINK · POSITION_REPORT · RECEIVED" in html
    # texte libre : coordonnées masquées, le reste gardé
    assert messages[1]["text"] == f"Rejoindre {aubelink.COORD_MASK} {aubelink.COORD_MASK} puis {aubelink.COORD_MASK}, batterie 72 %"
    assert messages[2]["text"] == "POSITION REQUEST"
    alert = route["beacons"][str(device["id"])]["alerts"][0]
    assert alert["message"] == f"Sortie de zone à {aubelink.COORD_MASK}, {aubelink.COORD_MASK}"


@pytest.mark.parametrize("raw, expected", [
    ("POSITION 45.50170N 073.56730W", "POSITION […] […]"),
    ("-73.5673 et +45.5017", "[…] et […]"),
    ("45° 30.1' N", "[…]"),
    ("45°30'06\"N 73°34'02\"O", "[…] […]"),
    ("STATUS NOMINAL\nBATTERY 72%\nALT 100 M\nHDG 270", "STATUS NOMINAL\nBATTERY 72%\nALT 100 M\nHDG 270"),
    ("Tension 15.2 V, version 1.12", "Tension 15.2 V, version 1.12"),
])
def test_masque_des_coordonnees(raw, expected):
    assert aubelink._masked(raw, 300) == expected


# ---------------------------------------------------------------------------
# 3. Accès
# ---------------------------------------------------------------------------

def test_acces_et_delegue(client, auth_client, beacon, make_user, fake, monkeypatch):
    u, _, device, _ = beacon
    owner = _acting(u)
    fake.add(owner, "AE-XR-001")
    # sans session : 401, comme les autres routes JSON AubeBeacon
    r = client.get(ROUTE)
    assert r.status_code == 401 and r.get_json()["error"] == "login_required"
    # compte non autorisé à voir AubeBeacon : 404 partout, aucune trace d'AubeLink
    plain = make_user("al_plain", role="both")
    c = auth_client(plain["id"])
    assert c.get(ROUTE).status_code == 404
    assert c.post(f"{PAGE}/{device['id']}/aubelink", data={"aubelink_drone_id": "AE-XR-001"}).status_code == 404
    assert c.post(f"{PAGE}/{device['id']}/aubelink/retirer").status_code == 404
    assert "aubelink" not in c.get("/espace", follow_redirects=True).get_data(as_text=True).lower()
    # autre pilote, autorisé par la liste d'accès : la balise d'autrui n'existe pas pour lui
    monkeypatch.setattr(config, "BEACON_ALLOWED_USERS", {plain["username"].lower()})
    assert c.post(f"{PAGE}/{device['id']}/aubelink", data={"aubelink_drone_id": "AE-XR-001"}).status_code == 404
    assert c.post(f"{PAGE}/{device['id']}/aubelink/retirer").status_code == 404
    assert str(device["id"]) not in c.get(ROUTE).get_json()["beacons"]
    assert all(call[2] != owner for call in fake.calls)
    # administrateur, vue ?all=1 : AubeLink est appelé au nom du propriétaire
    admin = _grant(make_user("al_admin", role="both"))
    ca = auth_client(admin["id"])
    j = ca.get(ROUTE + "?all=1").get_json()
    assert j["beacons"][str(device["id"])]["linked"] is False
    assert owner in {call[2] for call in fake.calls}
    assert _acting(admin) not in {call[2] for call in fake.calls}
    n = len(fake.calls)
    _link(ca, device["id"], "AE-XR-001", "?all=1")
    assert fake.calls[n] == ("GET", STATUS, owner, None)          # lecture fraîche : le drone est-il libre ?
    assert fake.calls[n + 1] == ("PUT", "/api/v1/drones/AE-XR-001/beacon", owner, {"beaconId": device["device_uid"]})


def test_actions_protegees_par_csrf(app, auth_client, beacon, fake, monkeypatch):
    for endpoint in ("beacon_views.device_aubelink_link", "beacon_views.device_aubelink_unlink"):
        assert endpoint not in security.CSRF_EXEMPT_ROUTES
    u, _, device, _ = beacon
    c = auth_client(u["id"])
    monkeypatch.setitem(app.config, "TESTING", False)
    assert c.post(f"{PAGE}/{device['id']}/aubelink", data={"aubelink_drone_id": "AE-XR-001"}).status_code == 403
    assert fake.calls == []


def test_actions_limitees_en_debit(app, auth_client, beacon, fake, monkeypatch):
    """Associer, retirer et supprimer appellent AubeLink sans cache : une
    boucle sur ces formulaires ne doit pas épuiser le budget partagé de la clé
    d'intégration (et mettre tout le monde en pause après le 429)."""
    import collections
    u, _, device, _ = beacon
    fake.add(_acting(u), "AE-XR-001")
    c = auth_client(u["id"])
    monkeypatch.setattr(security, "_buckets", collections.defaultdict(collections.deque))
    monkeypatch.setitem(app.config, "TESTING", False)
    with c.session_transaction() as sess:
        sess["_csrf"] = "jeton-de-test"
    form = {"csrf_token": "jeton-de-test", "aubelink_drone_id": "AE-XR-001"}
    link = [c.post(f"{PAGE}/{device['id']}/aubelink", data=form).status_code for _ in range(11)]
    assert link == [302] * 10 + [429]
    unlink = [c.post(f"{PAGE}/{device['id']}/aubelink/retirer", data=form).status_code for _ in range(11)]
    assert unlink == [302] * 10 + [429]
    # au-delà de la limite, AubeLink n'est plus appelé : associer fait 10 lectures
    # fraîches et 10 PUT, retirer 10 lectures fraîches et 1 DELETE
    assert fake.methods_since(0).count("PUT") == 10
    assert len(fake.calls) == 10 + 10 + 10 + 1
    # suppression : limite plus large, comptée avant tout accès (balise inconnue : 404)
    limit = views.DELETE_LIMIT["per_minute"]
    gone = [c.post(f"{PAGE}/999999999/supprimer", data=form).status_code for _ in range(limit + 1)]
    assert gone == [404] * limit + [429]


# ---------------------------------------------------------------------------
# 4. Associer
# ---------------------------------------------------------------------------

def test_associer(auth_client, beacon, fake):
    u, _, device, _ = beacon
    acting, uid = _acting(u), device["device_uid"]
    fake.add(acting, "AE-XR-001", name="Mavic")
    fake.add(acting, "AE-XR-002", name="Second")
    c = auth_client(u["id"])
    assert c.get(ROUTE).get_json()["beacons"][str(device["id"])]["linked"] is False
    n = len(fake.calls)
    html = _link(c, device["id"], " ae-xr-001 ").get_data(as_text=True)
    # lecture fraîche malgré le cache (le drone est-il libre ?), puis PUT
    assert fake.calls[n] == ("GET", STATUS, acting, None)
    assert fake.calls[n + 1] == ("PUT", "/api/v1/drones/AE-XR-001/beacon", acting, {"beaconId": uid})
    assert _flash("aubelink.paired", drone="AE-XR-001") in html
    # cache invalidé : la page qui suit relit AubeLink et montre l'association
    assert fake.calls[n + 2][:2] == ("GET", STATUS)
    assert f'data-aubelink-for="{device["id"]}"' in html and "Mavic · AE-XR-001" in html
    audit = db.fetchone("SELECT target FROM audit_log WHERE action='beacon_aubelink_link' ORDER BY id DESC LIMIT 1")
    assert audit["target"] == f"{uid}->AE-XR-001"


def test_associer_refus(auth_client, beacon, fake, make_user):
    u, _, device, _ = beacon
    acting, uid = _acting(u), device["device_uid"]
    fake.add(acting, "AE-XR-001", beacon_id=uid)
    fake.add(acting, "AE-XR-002")
    c = auth_client(u["id"])
    # 409 avec détail : la balise est déjà portée par un autre drone du même compte
    html = _link(c, device["id"], "AE-XR-002").get_data(as_text=True)
    assert _flash("aubelink.err.taken_by", drone="AE-XR-001") in html
    # 409 sans détail : portée par le drone d'un autre compte
    other, _ = devices.create_device(u["id"], label="Deuxième")
    fake.add("quelquun@aubemail.com", "AE-ZZ-001", beacon_id=other["device_uid"])
    html = _link(c, other["id"], "AE-XR-002").get_data(as_text=True)
    assert _flash("aubelink.err.taken") in html and "AE-ZZ-001" not in html
    # 404 : drone absent de ce compte
    html = _link(c, other["id"], "AE-NOPE-1").get_data(as_text=True)
    assert _flash("aubelink.err.drone_not_found") in html
    # identifiant invalide : aucun appel
    n = len(fake.calls)
    html = _link(c, other["id"], "ae t2").get_data(as_text=True)
    assert _flash("aubelink.err.invalid_drone") in html
    assert "PUT" not in fake.methods_since(n)
    # 403 : portée manquante, code d'AubeLink affiché
    fake.script = [_err(403, "SCOPE_MISSING")]
    html = _link(c, other["id"], "AE-XR-002").get_data(as_text=True)
    assert _flash("aubelink.err.refused", code="SCOPE_MISSING") in html


def test_associer_refuse_un_drone_qui_porte_une_autre_balise(auth_client, beacon, fake):
    """Le PUT d'AubeLink remplacerait sans erreur la balise que porte déjà le
    drone : la route le refuse d'après une lecture fraîche, même quand la page
    (et le cache) montraient encore ce drone libre."""
    u, _, device, _ = beacon
    acting, uid = _acting(u), device["device_uid"]
    fake.add(acting, "AE-XR-001", name="Mavic")
    c = auth_client(u["id"])
    assert '<option value="AE-XR-001">' in c.get(PAGE).get_data(as_text=True)   # libre, en cache
    # entre-temps, une autre balise est posée sur ce drone depuis AubeLink
    fake.drones[acting]["AE-XR-001"]["beaconId"] = "AUBE-BCN-777777"
    n = len(fake.calls)
    html = _link(c, device["id"], "AE-XR-001").get_data(as_text=True)
    assert _flash("aubelink.err.drone_busy", drone="AE-XR-001", uid="AUBE-BCN-777777") in html
    assert fake.calls[n] == ("GET", STATUS, acting, None) and "PUT" not in fake.methods_since(n)
    assert fake.drones[acting]["AE-XR-001"]["beaconId"] == "AUBE-BCN-777777"
    assert db.fetchone("SELECT 1 FROM audit_log WHERE action='beacon_aubelink_link' AND target=?",
                       (f"{uid}->AE-XR-001",)) is None
    # une autre balise du même compte : même refus (formulaire rejoué, autre onglet)
    other, _ = devices.create_device(u["id"], label="Deuxième")
    fake.drones[acting]["AE-XR-001"]["beaconId"] = other["device_uid"]
    n = len(fake.calls)
    html = _link(c, device["id"], "AE-XR-001").get_data(as_text=True)
    assert _flash("aubelink.err.drone_busy", drone="AE-XR-001", uid=other["device_uid"]) in html
    assert "PUT" not in fake.methods_since(n)
    # la balise que le drone porte déjà : association rejouée sans refus
    html = _link(c, other["id"], "AE-XR-001").get_data(as_text=True)
    assert _flash("aubelink.paired", drone="AE-XR-001") in html


# ---------------------------------------------------------------------------
# 5. Retirer
# ---------------------------------------------------------------------------

def test_retirer(auth_client, beacon, fake):
    u, _, device, _ = beacon
    acting, uid = _acting(u), device["device_uid"]
    fake.add(acting, "AE-XR-001", beacon_id=uid)
    c = auth_client(u["id"])
    c.get(ROUTE)                                   # lecture en cache
    n = len(fake.calls)
    html = _unlink(c, device["id"]).get_data(as_text=True)
    assert fake.calls[n][:2] == ("GET", STATUS)    # lecture fraîche malgré le cache
    assert fake.calls[n + 1] == ("DELETE", "/api/v1/drones/AE-XR-001/beacon", acting, None)
    assert _flash("aubelink.unpaired", drone="AE-XR-001") in html
    assert fake.drones[acting]["AE-XR-001"]["beaconId"] is None
    # plus associée : aucun DELETE
    n = len(fake.calls)
    html = _unlink(c, device["id"]).get_data(as_text=True)
    assert "DELETE" not in fake.methods_since(n)
    assert _flash("aubelink.err.not_linked") in html


# ---------------------------------------------------------------------------
# 6. Pannes
# ---------------------------------------------------------------------------

def test_panne_reseau_puis_pause(auth_client, beacon, fake, clock):
    u, _, device, _ = beacon
    fake.add(_acting(u), "AE-XR-001", beacon_id=device["device_uid"])
    fake.script = [Resp(0, None, None)]
    c = auth_client(u["id"])
    r = c.get(PAGE)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and f'data-aubelink-for="{device["id"]}"' in html
    assert _flash("aubelink.unavailable") in html and "aubelink_drone_id" not in html
    j = c.get(ROUTE).get_json()
    assert j["available"] is False and j["reason"] == "unreachable"
    assert j["beacons"][str(device["id"])] == {"uid": device["device_uid"], "linked": None, "reason": "unreachable"}
    assert len(fake.calls) == 1                    # seconde lecture dans la pause : aucun appel
    clock[0] += config.AUBELINK_FAIL_CACHE_S + 1
    j = c.get(ROUTE).get_json()
    assert j["available"] is True and j["beacons"][str(device["id"])]["linked"] is True
    assert len(fake.calls) == 2


def test_limite_de_debit_retry_after(auth_client, beacon, fake, clock):
    u, _, device, _ = beacon
    fake.add(_acting(u), "AE-XR-001")
    fake.script = [Resp(429, {"error": {"code": "RATE_LIMITED", "message": "trop de requêtes"}}, "5")]
    c = auth_client(u["id"])
    j = c.get(ROUTE).get_json()
    assert j["available"] is False and j["beacons"][str(device["id"])]["reason"] == "rate_limited"
    clock[0] += 4
    assert c.get(ROUTE).get_json()["reason"] == "rate_limited" and len(fake.calls) == 1
    clock[0] += 2
    assert c.get(ROUTE).get_json()["available"] is True and len(fake.calls) == 2
    # sur une action : message « très sollicité »
    fake.script = [Resp(429, {"error": {"code": "RATE_LIMITED", "message": "x"}}, None)]
    html = _link(c, device["id"], "AE-XR-001").get_data(as_text=True)
    assert _flash("aubelink.busy") in html


def test_cle_refusee_journal_et_pause(auth_client, beacon, fake, clock, caplog):
    u, _, device, _ = beacon
    fake.script = [_err(401, "INVALID_API_KEY")]
    c = auth_client(u["id"])
    with caplog.at_level(logging.INFO, logger="aubepilot.beacon.aubelink"):
        j = c.get(ROUTE).get_json()
    assert j["beacons"][str(device["id"])]["reason"] == "misconfigured"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(m.startswith("aubelink.key_rejected") for m in errors)
    assert any("aubelink.request_failed" in m and "status=401" in m and "INVALID_API_KEY" in m for m in errors)
    assert KEY not in caplog.text and "alp_" not in caplog.text
    clock[0] += 299
    c.get(ROUTE)
    assert len(fake.calls) == 1
    # sur une action, le pilote lit « ne répond pas », jamais le détail de la clé
    fake.script = [_err(401, "INVALID_API_KEY")]
    html = _link(c, device["id"], "AE-XR-001").get_data(as_text=True)
    assert _flash("aubelink.err.unreachable") in html


def test_refus_en_lecture_cache_negatif(auth_client, beacon, fake, clock, caplog):
    u, _, device, _ = beacon
    fake.script = [_err(403, "INVALID_ACTING_USER")]
    c = auth_client(u["id"])
    with caplog.at_level(logging.ERROR, logger="aubepilot.beacon.aubelink"):
        j = c.get(ROUTE).get_json()
    assert j["beacons"][str(device["id"])]["reason"] == "refused"
    assert any("INVALID_ACTING_USER" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    c.get(ROUTE)
    assert len(fake.calls) == 1                    # cache négatif pour ce délégué
    clock[0] += config.AUBELINK_FAIL_CACHE_S + 1
    c.get(ROUTE)
    assert len(fake.calls) == 2


def test_budget_epuise_differe(app_ctx, beacon, fake):
    u, _, device, _ = beacon
    rows = devices.list_devices(u["id"])
    out = aubelink.panels(rows, budget_s=0)
    assert fake.calls == [] and out["available"] is True
    assert out["beacons"][str(device["id"])] == {"uid": device["device_uid"], "linked": None, "reason": "deferred"}


def _owners(make_user, fake, count, name):
    """`count` pilotes, chacun avec une balise et un drone AubeLink libre."""
    owners = []
    for _ in range(count):
        u = make_user(name, role="both")
        devices.create_device(u["id"], label="Balise")
        fake.add(_acting(u), "AE-XR-001")
        owners.append(u)
    return owners


def test_budget_jamais_de_delai_raccourci(app_ctx, make_user, fake, clock, monkeypatch):
    """AubeLink sain mais lent (1,4 s par lecture, sous le délai de 2,5 s), vue
    administrateur à trois propriétaires, budget de 4 s. Un délai raccourci au
    budget restant (1,2 s pour le troisième) expirerait : statut 0, puis pause
    de tout le worker alors qu'AubeLink répond. Le troisième propriétaire
    attend donc le sondage suivant, sans pause."""
    owners = _owners(make_user, fake, 3, "al_lent")
    delays = []

    def slow(method, path, acting, body=None, timeout=None):
        # comme urllib : une réponse plus lente que le délai reçu donne un statut 0
        delay = timeout if timeout is not None else aubelink._timeout_s()
        delays.append(delay)
        if delay < 1.4:
            clock[0] += delay
            return Resp(0, None, None)
        clock[0] += 1.4
        return fake(method, path, acting, body)

    monkeypatch.setattr(aubelink, "_request", slow)
    rows = [d for u in owners for d in devices.list_devices(u["id"])]
    start = clock[0]
    out = aubelink.panels(rows, budget_s=4.0)
    assert delays == [aubelink._timeout_s()] * 2              # jamais un délai raccourci
    assert clock[0] - start <= 4.0                             # budget tenu
    assert out["available"] is True and out["reason"] is None
    assert [out["beacons"][str(d["id"])].get("linked") for d in rows] == [False, False, None]
    assert out["beacons"][str(rows[2]["id"])]["reason"] == "deferred"
    assert aubelink._paused(clock[0]) is None                  # aucune pause posée
    # le propriétaire différé est servi au sondage suivant, les autres viennent du cache
    out = aubelink.panels(rows, budget_s=4.0)
    assert out["beacons"][str(rows[2]["id"])]["linked"] is False and len(delays) == 3


def test_sondages_successifs_lisent_tous_les_proprietaires(app_ctx, make_user, fake, clock, monkeypatch):
    """Vue administrateur à cinq propriétaires, AubeLink sain mais lent (1,4 s) :
    le budget de 4 s n'en couvre que deux par sondage. Les moins récemment lus
    passent d'abord, donc en trois sondages chacun a été lu ; sans cet ordre, les
    trois derniers restaient « deferred » à chaque sondage."""
    owners = _owners(make_user, fake, 5, "al_tour")
    read = []

    def slow(method, path, acting, body=None, timeout=None):
        clock[0] += 1.4
        read.append(acting)
        return fake(method, path, acting, body)

    monkeypatch.setattr(aubelink, "_request", slow)
    rows = [d for u in owners for d in devices.list_devices(u["id"])]
    for _ in range(3):
        aubelink.panels(rows, budget_s=4.0)
        clock[0] += config.AUBELINK_CACHE_S + 1          # sondage suivant : le cache a expiré
    assert len(read) == 6 and len(set(read)) == 5


def test_saisie_manuelle_de_la_serie_au_dela_refusee(app_ctx, pilot_with_drone):
    """Un numéro de la série saisi à la main au-delà du plus grand attribué
    (« 999999 ») décalerait la numérotation automatique pour toujours : refusé.
    Un numéro déjà attribué puis retiré se réenregistre (même balise physique)."""
    u, _ = pilot_with_drone
    top = devices._serial_top()
    for raw in (f"{top + 5:06d}", "999999"):
        with pytest.raises(devices.DeviceError):
            devices.create_device(u["id"], device_uid=raw)
    assert devices._serial_top() == top
    d, _ = devices.create_device(u["id"])
    assert devices.delete_device(d["id"], u["id"])
    again, _ = devices.create_device(u["id"], device_uid=d["device_uid"][len(devices.UID_PREFIX):])
    assert again["device_uid"] == d["device_uid"]
    devices.delete_device(again["id"], u["id"])


def test_budget_couvre_toujours_un_appel(app_ctx, beacon, fake, monkeypatch):
    """Un AUBELINK_TIMEOUT_MS plus long que le budget de la page ne laisse pas
    tout en attente : le budget couvre toujours un appel entier."""
    u, _, device, _ = beacon
    monkeypatch.setattr(config, "AUBELINK_TIMEOUT_MS", 5000)
    out = aubelink.panels(devices.list_devices(u["id"]), budget_s=views.AUBELINK_PAGE_BUDGET_S)
    assert out["beacons"][str(device["id"])]["linked"] is False and len(fake.calls) == 1


def test_lectures_simultanees_bornees(app_ctx, make_user, fake, monkeypatch):
    """Un AubeLink lent n'occupe jamais plus de MAX_CONCURRENT_READS fils par
    worker : au-delà, `deferred` tout de suite, sans appel ni attente."""
    limit = aubelink.MAX_CONCURRENT_READS
    monkeypatch.setattr(aubelink, "_reads", threading.BoundedSemaphore(limit))
    owners = _owners(make_user, fake, limit + 1, "al_simult")
    rows = {u["id"]: devices.list_devices(u["id"]) for u in owners}
    entered, release, seen = threading.Semaphore(0), threading.Event(), []

    def blocked(method, path, acting, body=None, **kwargs):
        seen.append(acting)
        entered.release()
        release.wait(10)
        return fake(method, path, acting, body)

    monkeypatch.setattr(aubelink, "_request", blocked)
    results = {}
    threads = [threading.Thread(target=lambda u=u: results.update({u["id"]: aubelink.panels(rows[u["id"]], 4.0)}))
               for u in owners[:limit]]
    last = owners[limit]
    dev = rows[last["id"]][0]
    try:
        for t in threads:
            t.start()
        for _ in range(limit):
            assert entered.acquire(timeout=10)                 # `limit` lectures attendent AubeLink
        t0 = time.monotonic()
        out = aubelink.panels(rows[last["id"]], budget_s=4.0)
        assert time.monotonic() - t0 < 1.0                     # aucune attente
        assert out["available"] is True
        assert out["beacons"][str(dev["id"])] == {"uid": dev["device_uid"], "linked": None, "reason": "deferred"}
        assert _acting(last) not in seen and len(seen) == limit
    finally:
        release.set()
        for t in threads:
            t.join(10)
    assert all(results[u["id"]]["available"] for u in owners[:limit])
    # jetons rendus : la lecture suivante passe
    out = aubelink.panels(rows[last["id"]], budget_s=4.0)
    assert out["beacons"][str(dev["id"])]["linked"] is False


# ---------------------------------------------------------------------------
# 7. Suppression d'une balise
# ---------------------------------------------------------------------------

def test_suppression_dissocie_dans_aubelink(auth_client, beacon, fake):
    u, _, device, _ = beacon
    acting, uid = _acting(u), device["device_uid"]
    fake.add(acting, "AE-XR-001", beacon_id=uid)
    c = auth_client(u["id"])
    c.post(f"{PAGE}/{device['id']}/supprimer", follow_redirects=True)
    assert ("DELETE", "/api/v1/drones/AE-XR-001/beacon", acting, None) in fake.calls
    assert fake.drones[acting]["AE-XR-001"]["beaconId"] is None
    assert devices.get_by_uid(uid) is None


def test_suppression_malgre_panne(auth_client, beacon, fake):
    u, _, device, _ = beacon
    fake.add(_acting(u), "AE-XR-001", beacon_id=device["device_uid"])
    fake.script = [Resp(0, None, None)]
    c = auth_client(u["id"])
    html = c.post(f"{PAGE}/{device['id']}/supprimer", follow_redirects=True).get_data(as_text=True)
    assert devices.get_by_uid(device["device_uid"]) is None
    assert _flash("aubelink.delete_orphan") in html


def test_compte_supprime_jamais_delegue(app_ctx, auth_client, beacon, make_user, fake):
    """Un compte supprimé garde ses balises, mais `users.email` devient
    deleted-<id>@invalid.local : AubePilot n'agit plus en son nom (aucun appel,
    ni profil fantôme deleted-<id>@aubemail.com chez AubeLink)."""
    u, _, device, _ = beacon
    owner = _acting(u)
    fake.add(owner, "AE-XR-001", beacon_id=device["device_uid"])
    assert services.delete_account(u["id"])["ok"]
    row = devices.get_device(device["id"], None)
    assert row["owner_email"] == f"deleted-{u['id']}@invalid.local" and row["owner_deleted_at"]
    # le délégué vient de l'identifiant, jamais de l'adresse réécrite
    assert aubelink.acting_email(row["owner_username"], row["owner_email"]) == owner
    assert aubelink.device_acting(row) is None

    admin = _grant(make_user("al_admin_sup", role="both"))
    ca = auth_client(admin["id"])
    j = ca.get(ROUTE + "?all=1").get_json()
    assert j["beacons"][str(device["id"])] == {"uid": device["device_uid"], "linked": None, "reason": "owner_deleted"}
    html = ca.get(PAGE + "?all=1").get_data(as_text=True)
    assert _flash("aubelink.owner_deleted") in html
    # actions refusées sans appel ; la suppression passe, association laissée orpheline
    html = _link(ca, device["id"], "AE-XR-001", "?all=1").get_data(as_text=True)
    assert _flash("aubelink.owner_deleted") in html
    html = _unlink(ca, device["id"], "?all=1").get_data(as_text=True)
    assert _flash("aubelink.owner_deleted") in html
    html = ca.post(f"{PAGE}/{device['id']}/supprimer?all=1", follow_redirects=True).get_data(as_text=True)
    assert devices.get_by_uid(device["device_uid"]) is None
    assert _flash("aubelink.delete_orphan") in html
    assert all(call[2] != owner and "deleted-" not in call[2] for call in fake.calls)


# ---------------------------------------------------------------------------
# 8. Numéros de balise
# ---------------------------------------------------------------------------

def test_numero_jamais_reattribue(app_ctx, pilot_with_drone):
    u, _ = pilot_with_drone
    d, _ = devices.create_device(u["id"])
    n = int(d["device_uid"][len(devices.UID_PREFIX):])
    assert devices.delete_device(d["id"], u["id"])
    assert db.fetchone("SELECT 1 FROM beacon_retired_uids WHERE device_uid=?", (d["device_uid"],))
    assert devices.next_uid() == f"{devices.UID_PREFIX}{n + 1:06d}"
    # la même balise physique peut être réenregistrée à la main
    again, _ = devices.create_device(u["id"], device_uid=d["device_uid"])
    assert again["device_uid"] == d["device_uid"]


def test_numerotation_ignore_les_saisies_manuelles(app_ctx, pilot_with_drone):
    """Un numéro saisi à la main (numéro matériel de 12 chiffres tapé par
    erreur), puis retiré à jamais par la suppression, ne décale pas la série
    automatique et ne la fait pas déborder de UID_RE (13 chiffres, refusés
    par la balise comme par AubeLink)."""
    u, _ = pilot_with_drone
    expected = devices.next_uid()
    manual = [devices.create_device(u["id"], device_uid=raw)[0]
              for raw in ("861234567890", "999999999999", "1234567", "SIM4242")]
    try:
        assert devices.next_uid() == expected
        for d in manual:
            assert devices.delete_device(d["id"], u["id"])
        assert db.fetchone("SELECT 1 FROM beacon_retired_uids WHERE device_uid='AUBE-BCN-999999999999'")
        assert devices.next_uid() == expected
        d, _ = devices.create_device(u["id"])
        assert d["device_uid"] == expected and devices.UID_RE.match(expected)
    finally:
        db.execute("DELETE FROM beacon_retired_uids WHERE device_uid IN (?, ?, ?, ?)",
                   tuple(d["device_uid"] for d in manual))


def test_numerotation_epuisee(app_ctx, pilot_with_drone):
    """Au-delà de AUBE-BCN-999999, DeviceError plutôt qu'un identifiant que
    UID_RE refuserait ; la saisie manuelle reste possible."""
    u, _ = pilot_with_drone
    last = devices.UID_PREFIX + "9" * devices.SERIAL_DIGITS
    db.execute("INSERT INTO beacon_retired_uids (device_uid, retired_at) VALUES (?, ?)", (last, devices.now_iso()))
    try:
        with pytest.raises(devices.DeviceError):
            devices.next_uid()
        with pytest.raises(devices.DeviceError):
            devices.create_device(u["id"])
        d, _ = devices.create_device(u["id"], device_uid="SIM424242")
        assert d["device_uid"] == "AUBE-BCN-SIM424242"
    finally:
        db.execute("DELETE FROM beacon_retired_uids WHERE device_uid=?", (last,))


@pytest.mark.parametrize("raw, expected", [
    ("AUBE-BCN-000001", "AUBE-BCN-000001"),
    (" aube-bcn-sim001 ", "AUBE-BCN-SIM001"),
    ("000001", "AUBE-BCN-000001"),
    ("sim001", "AUBE-BCN-SIM001"),
])
def test_normalisation_vecteurs_partages(raw, expected):
    # Mêmes vecteurs qu'AubeLink (normalizeBeaconId, packages/types/src/beacon.ts).
    assert devices.normalize_uid(raw) == expected


@pytest.mark.parametrize("raw", ["12", "AUBE-BCN-12", "BEACON-AE-0291", "AUBE-BCN-0000000000001",
                                 "AUBE-BCN-AB_1", ""])
def test_normalisation_vecteurs_invalides(raw):
    with pytest.raises(devices.DeviceError):
        devices.normalize_uid(raw)


# ---------------------------------------------------------------------------
# Client : fonction réseau réelle (serveur HTTP local) et cache
# ---------------------------------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def _reply(self, status, body=b"", ctype="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        _Handler.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if self.path == STATUS:
            self._reply(200, json.dumps({"serverTime": "t", "drones": [], "truncated": False}).encode())
        elif self.path == "/api/v1/drones/AE%2FX%3F1/beacon":
            self._reply(409, json.dumps({"error": {"code": "BEACON_ALREADY_LINKED", "message": "m",
                                                   "details": {"droneId": "AE-XR-001"}}}).encode())
        elif self.path == "/html":
            self._reply(502, b"<html>bad gateway</html>", "text/html")
        elif self.path == "/redirect":
            self._reply(302, b"", headers={"Location": "http://127.0.0.1:1/ailleurs"})
        elif self.path == "/limite":
            self._reply(429, json.dumps({"error": {"code": "RATE_LIMITED", "message": "m"}}).encode(),
                        headers={"Retry-After": "7"})
        else:
            self._reply(204)

    do_GET = do_PUT = do_DELETE = _handle


@pytest.fixture()
def local_server(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.seen = []
    monkeypatch.setattr(config, "AUBELINK_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setattr(config, "AUBELINK_KEY", KEY)
    yield server
    server.shutdown()
    server.server_close()


def test_request_reel(local_server, monkeypatch):
    acting = "xros@aubemail.com"
    r = aubelink._request("GET", STATUS, acting)
    assert r.status == 200 and r.data["drones"] == []
    method, path, headers, _ = _Handler.seen[-1]
    assert headers["authorization"] == f"Bearer {KEY}" and headers["x-acting-user"] == acting
    assert headers["accept"] == "application/json" and headers["user-agent"] == "AubePilot"
    assert "content-type" not in headers
    # segment encodé, corps JSON, erreur HTTP lue comme du JSON
    r = aubelink._request("PUT", f"/api/v1/drones/{aubelink._seg('AE/X?1')}/beacon", acting, {"beaconId": "AUBE-BCN-000001"})
    method, path, headers, body = _Handler.seen[-1]
    assert (method, path) == ("PUT", "/api/v1/drones/AE%2FX%3F1/beacon")
    assert headers["content-type"] == "application/json" and json.loads(body) == {"beaconId": "AUBE-BCN-000001"}
    assert r.status == 409 and r.data["error"]["details"] == {"droneId": "AE-XR-001"}
    # 204 sans corps, 429 avec Retry-After
    assert aubelink._request("DELETE", "/rien", acting) == Resp(204, None, None)
    assert aubelink._request("GET", "/limite", acting).retry_after == "7"
    # réponse illisible ou serveur absent : status 0 ; redirection jamais suivie (la clé
    # ne part pas ailleurs), lue comme un échec (unreachable)
    assert aubelink._request("GET", "/html", acting).status == 0
    assert aubelink._request("GET", "/redirect", acting) == Resp(302, None, None)
    assert [s for s in _Handler.seen if s[1] == "/ailleurs"] == []
    monkeypatch.setattr(config, "AUBELINK_URL", "http://127.0.0.1:9")
    assert aubelink._request("GET", STATUS, acting).status == 0
    with pytest.raises(ValueError):
        aubelink._request("GET", STATUS, "")


def test_cache_borne(monkeypatch, clock):
    monkeypatch.setattr(aubelink, "CACHE_MAX", 3)
    for i in range(5):
        clock[0] += 1
        aubelink._cache_put(f"u{i}@aubemail.com", {"drones": []}, 60)
    assert len(aubelink._cache) == 3
    assert set(aubelink._cache) == {"u2@aubemail.com", "u3@aubemail.com", "u4@aubemail.com"}


def test_flash_key_correspondance():
    E = aubelink.AubeLinkError
    assert aubelink.flash_key(E("already_linked", 409, {"droneId": "AE-XR-001"})) == ("aubelink.err.taken_by", {"drone": "AE-XR-001"})
    assert aubelink.flash_key(E("already_linked", 409, {"droneId": "<script>"})) == ("aubelink.err.taken", {})
    assert aubelink.flash_key(E("refused", 403, None, "BEACON_LINK_VIA_AUBEPILOT")) == (
        "aubelink.err.refused", {"code": "BEACON_LINK_VIA_AUBEPILOT"})
    assert aubelink.flash_key(E("misconfigured", 401))[0] == "aubelink.err.unreachable"
    assert aubelink.flash_key(E("invalid", 400))[0] == "aubelink.err.invalid_drone"
    assert aubelink.flash_key(E("not_found", 404))[0] == "aubelink.err.drone_not_found"


# ---------------------------------------------------------------------------
# Contrôle après déploiement (scripts/aubelink_check.py)
# ---------------------------------------------------------------------------

def _check_script():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "aubelink_check.py"
    spec = importlib.util.spec_from_file_location("aubelink_check_teste", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_controle_lit_l_environnement_comme_systemd(tmp_path, monkeypatch, capsys):
    """/etc/aubepilot.env lu comme systemd le lit pour le service : dernière
    occurrence retenue (la clé ajoutée par `>>` après une ligne vide),
    guillemets retirés ; l'environnement d'appel garde la main."""
    check = _check_script()
    env_file = tmp_path / "aubepilot.env"
    env_file.write_text(
        "# /etc/aubepilot.env\n"
        "AUBELINK_URL=http://10.8.0.2:5125\n"
        "AUBELINK_KEY=\n"
        "; commentaire\n"
        'AUBELINK_KEY="alp_1_essai"\n'
        "AUBELINK_PUBLIC_URL='https://link.aubeetoilee.com'\n"
        "DEJA=fichier\n", encoding="utf-8")
    environ = {"DEJA": "appel"}
    assert check.load_env_file(str(env_file), environ) is None
    assert environ == {"DEJA": "appel", "AUBELINK_URL": "http://10.8.0.2:5125",
                       "AUBELINK_KEY": "alp_1_essai", "AUBELINK_PUBLIC_URL": "https://link.aubeetoilee.com"}
    # fichier absent (poste de développement) : environnement d'appel seul
    assert check.load_env_file(str(tmp_path / "absent.env"), environ) is None
    # fichier présent mais illisible : signalé, code 1, jamais « non configuré »
    env_file.chmod(0)
    try:
        if os.access(env_file, os.R_OK):
            pytest.skip("compte qui lit tout (root) : illisibilité non reproductible")
        message = check.load_env_file(str(env_file), {})
        assert message and "illisible" in message and str(env_file) in message
        monkeypatch.setattr(check, "ENV_FILE", str(env_file))
        monkeypatch.setattr(sys, "argv", ["aubelink_check.py", "--user", "xros"])
        assert check.main() == 1
        err = capsys.readouterr().err
        assert "illisible" in err and "non configuré" not in err
    finally:
        env_file.chmod(0o600)
