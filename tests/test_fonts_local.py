"""Polices servies par le site (scripts/self_host_fonts.py), plus rien chez
Google : ni feuille de style, ni fichier, ni autorisation dans la CSP.
Politique de confidentialite v1.5 (2026-10-07) : « leur affichage ne fait
connaitre votre adresse IP a aucun tiers » ; ce test tient cette promesse."""
import os
import re

import pytest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


@pytest.mark.parametrize("path", ["/", "/ur/", "/ar/", "/bn/", "/am/", "/ta/", "/th/", "/fa/"])
def test_aucune_police_google(client, path):
    r = client.get(path)
    html = r.data.decode()
    assert "googleapis" not in html and "gstatic" not in html
    assert "/static/css/fonts.css" in html
    csp = r.headers.get("Content-Security-Policy", "")
    assert "googleapis" not in csp and "gstatic" not in csp


def test_police_propre_a_l_ecriture(client):
    # « / » d'abord : visiter /ur/ pose le cookie de langue, qui redirige ensuite.
    head = client.get("/").data.decode().split("</head>", 1)[0]
    assert "/static/css/fonts-" not in head
    assert "/static/css/fonts-ur.css" in client.get("/ur/").data.decode()
    assert "/static/css/fonts-arab.css" in client.get("/fa/").data.decode()


def test_chaque_fichier_de_police_existe():
    sheets = [f for f in os.listdir(os.path.join(STATIC, "css")) if re.match(r"fonts(-\w+)?\.css$", f)]
    assert len(sheets) == 7
    for name in sheets:
        css = open(os.path.join(STATIC, "css", name), encoding="utf-8").read()
        urls = re.findall(r"url\(([^)]+)\)", css)
        assert urls and all(u.startswith("/static/fonts/") for u in urls), name
        for u in urls:
            assert os.path.isfile(os.path.join(STATIC, u[len("/static/"):])), u
