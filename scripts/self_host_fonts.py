"""Rapatrie les polices Google Fonts dans static/fonts/ (a relancer seulement
pour changer de police ou de graisses).

Pourquoi : servies par le site, les polices ne font plus connaitre l'adresse
IP des visiteurs a Google (politique de confidentialite v1.5), evitent deux
connexions de plus au chargement, et leur feuille de style part compressee et
en cache comme le reste de /static/.

Ce que fait le script : demande a Google les feuilles CSS avec l'en-tete d'un
navigateur recent (woff2 decoupes par alphabet, unicode-range), telecharge
chaque fichier sous static/fonts/<famille>/<version>/, puis ecrit la feuille
reecrite en adresses locales :
  static/css/fonts.css          polices de toutes les pages
  static/css/fonts-<lang>.css   police propre a une ecriture (ur, ar/fa, ...)

    .venv/bin/python scripts/self_host_fonts.py
"""
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

SHEETS = {
    "fonts": "family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400;1,500"
             "&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700",
    "fonts-ur": "family=Noto+Nastaliq+Urdu:wght@400;600;700",
    "fonts-arab": "family=Noto+Naskh+Arabic:wght@400;500;600;700",
    "fonts-bn": "family=Noto+Sans+Bengali:wght@400;500;600;700",
    "fonts-am": "family=Noto+Sans+Ethiopic:wght@400;500;600;700",
    "fonts-ta": "family=Noto+Sans+Tamil:wght@400;500;600;700",
    "fonts-th": "family=Noto+Sans+Thai:wght@400;500;600;700",
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main() -> int:
    for name, query in SHEETS.items():
        css = get(f"https://fonts.googleapis.com/css2?{query}&display=swap").decode()

        def local(m):
            url = m.group(1)
            rel = url.split("fonts.gstatic.com/s/", 1)[1]
            dst = os.path.join(STATIC, "fonts", rel)
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(get(url))
            return f"url(/static/fonts/{rel})"

        css = re.sub(r"url\((https://fonts\.gstatic\.com/s/[^)]+)\)", local, css)
        if "fonts.gstatic.com" in css:
            print(f"{name}: adresse Google restante", file=sys.stderr)
            return 1
        head = "/* Polices hébergées par le site (scripts/self_host_fonts.py). Licence SIL OFL. */\n"
        with open(os.path.join(STATIC, "css", f"{name}.css"), "w", encoding="utf-8") as f:
            f.write(head + css)
        print(name, css.count("@font-face"), "faces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
