"""App Links Android : /.well-known/assetlinks.json doit declarer l'app et
les certificats qui la signent, sinon les liens pilot.aubeetoilee.com
s'ouvrent dans le navigateur au lieu de l'app.
"""
import re

# Cle d'importation Play (mobile/cles/cle-android.jks), APK et AAB >= 1.5.0.
UPLOAD_KEY = ("66:B8:84:07:3B:08:3D:C5:3A:D2:80:09:1F:B4:05:80:43:8F:E9:72:"
              "A9:8E:B0:3F:52:0F:A6:A4:3E:52:0C:2D")
# Certificat de debug local, qui signait les APK jusqu'a la 1.4.1.
DEBUG_KEY = ("D4:F7:87:FE:6B:A8:3D:76:04:A1:9E:FF:EF:27:24:E4:73:8E:BE:ED:"
             "F8:E8:85:93:97:DC:9C:D4:10:15:5C:CC")


def test_assetlinks_declare_app_et_certificats(client):
    r = client.get("/.well-known/assetlinks.json")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    (entry,) = r.get_json()
    assert entry["relation"] == ["delegate_permission/common.handle_all_urls"]
    target = entry["target"]
    assert target["namespace"] == "android_app"
    assert target["package_name"] == "com.aubeetoilee.aubepilot"
    prints = target["sha256_cert_fingerprints"]
    assert UPLOAD_KEY in prints
    assert DEBUG_KEY in prints
    for fp in prints:
        assert re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){31}", fp), fp


def test_apple_app_site_association_declare_app_ios(client):
    """Liens universels iOS : l'app iPhone (equipe Apple + bundle id) doit
    figurer dans le fichier, servi en JSON a l'adresse exacte, sans .json."""
    r = client.get("/.well-known/apple-app-site-association")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    (detail,) = r.get_json()["applinks"]["details"]
    assert detail["appIDs"] == ["A78LGGU44D.com.aubeetoilee.aubepilot"]
    comps = detail["components"]
    # Le joker final ouvre tout le reste du site dans l'app ; les exclusions
    # doivent le preceder (iOS s'arrete a la premiere regle qui correspond).
    assert comps[-1] == {"/": "*"}
    excluded = {c["/"] for c in comps if c.get("exclude")}
    assert {"/static/*", "/media/*"} <= excluded
