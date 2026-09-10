"""Controle d'indexabilite : ce que Google verra s'il vient.

    .venv/bin/python scripts/check_indexation.py                  # la prod
    .venv/bin/python scripts/check_indexation.py http://127.0.0.1:5034
    .venv/bin/python scripts/check_indexation.py --all            # toutes les URL

Ce script ne dit PAS si une page est indexee : seul Google le sait (Search
Console > Pages, ou l'inspection d'URL). Il dit si quelque chose l'empeche
de l'etre, ce qui est la moitie du probleme et la seule verifiable d'ici :

  - le sitemap est joignable et bien forme ;
  - chaque page repond 200 ;
  - sa canonique pointe sur elle-meme (sinon Google indexe l'autre) ;
  - elle n'est pas en noindex ;
  - robots.txt ne l'interdit pas ;
  - les alternates hreflang se repondent (A pointe B et B pointe A).

Par defaut on echantillonne quelques URL par type ; --all les prend toutes.
"""
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from xml.etree import ElementTree

DEFAULT_BASE = "https://pilot.aubeetoilee.com"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
      "xhtml": "http://www.w3.org/1999/xhtml"}
UA = "AubePilot-indexation-check/1.0"
SAMPLE_PER_KIND = 3


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def kind_of(path):
    """Regroupe les URL par famille, pour echantillonner sans tout charger."""
    bare = re.sub(r"^/(?:en|es|ru|hi|uk|tr|ur|bn)(?=/|$)", "", path) or "/"
    if re.fullmatch(r"/pilotes/\d+", bare):
        return "fiche pilote"
    if re.fullmatch(r"/missions/\d+", bare):
        return "mission"
    if bare.startswith("/pilotes/pays/"):
        return "page pays/ville"
    if bare.startswith("/pilotes/specialite/"):
        return "page specialite"
    return "page fixe"


def check_page(url):
    """(ok, details) pour une URL : statut, canonique, robots, alternates."""
    try:
        status, html = fetch(url)
    except urllib.error.HTTPError as exc:
        return False, _detail(f"HTTP {exc.code}")
    except Exception as exc:
        return False, _detail(f"injoignable ({exc})")
    if status != 200:
        return False, _detail(f"HTTP {status}")

    canonical = re.search(r'rel="canonical" href="([^"]+)"', html)
    robots = re.search(r'name="robots" content="([^"]+)"', html)
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    alternates = dict(re.findall(r'hreflang="([\w-]+)" href="([^"]+)"', html))

    problemes = []
    if not canonical:
        problemes.append("pas de canonique")
    elif canonical.group(1) != url:
        problemes.append(f"canonique -> {canonical.group(1)}")
    if robots and "noindex" in robots.group(1):
        problemes.append("noindex")
    if not title or not title.group(1).strip():
        problemes.append("titre vide")
    return not problemes, _detail(" ; ".join(problemes),
                                  titre=(title.group(1).strip() if title else "")[:70],
                                  alternates=alternates)


def _detail(probleme, titre="", alternates=None):
    return {"probleme": probleme, "titre": titre, "alternates": alternates or {}}


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    every = "--all" in argv
    base = (args[0] if args else DEFAULT_BASE).rstrip("/")

    print(f"Base : {base}\n")

    # --- robots.txt
    try:
        _status, robots_txt = fetch(base + "/robots.txt")
        interdits = [l.split(":", 1)[1].strip()
                     for l in robots_txt.splitlines() if l.lower().startswith("disallow:")]
        sitemaps = [l.split(":", 1)[1].strip()
                    for l in robots_txt.splitlines() if l.lower().startswith("sitemap:")]
        print(f"robots.txt      OK · {len(interdits)} interdictions · sitemap declare : "
              f"{'oui' if sitemaps else 'NON'}")
    except Exception as exc:
        print(f"robots.txt      INJOIGNABLE ({exc})")
        interdits = []

    # --- sitemaps
    try:
        _status, xml = fetch(base + "/sitemap.xml")
        root = ElementTree.fromstring(xml)
    except Exception as exc:
        print(f"sitemap.xml     INJOIGNABLE ({exc})")
        return 1

    fichiers = [e.text for e in root.findall(".//sm:sitemap/sm:loc", NS) if e.text] \
        or [base + "/sitemap.xml"]
    print(f"sitemap.xml     OK · {len(fichiers)} fichier(s)")

    urls, alternates_declares = [], {}
    for f in fichiers:
        try:
            _status, sub = fetch(f)
            sroot = ElementTree.fromstring(sub)
        except Exception as exc:
            print(f"  {f} INJOIGNABLE ({exc})")
            continue
        for u in sroot.findall("sm:url", NS):
            node = u.find("sm:loc", NS)
            loc = node.text if node is not None else None
            if not loc:
                continue
            urls.append(loc)
            alternates_declares[loc] = {
                a.get("hreflang"): a.get("href")
                for a in u.findall("xhtml:link", NS) if a.get("hreflang") != "x-default"
            }
        print(f"  {f.rsplit('/', 1)[-1]:<20} {len(sroot.findall('sm:url', NS)):>5} URL")

    if not urls:
        print("\nAucune URL au sitemap : rien a indexer.")
        return 1

    # --- regroupement + echantillon
    par_type = defaultdict(list)
    for u in urls:
        par_type[kind_of(urllib.parse.urlparse(u).path)].append(u)

    print(f"\n{len(urls)} URL au total :")
    for k, v in sorted(par_type.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(v):>5}  {k}")

    a_tester = urls if every else [u for v in par_type.values() for u in v[:SAMPLE_PER_KIND]]
    print(f"\nControle de {len(a_tester)} page(s){'' if every else ' (echantillon, --all pour tout)'} :\n")

    ko, reciprocite_ko = [], []
    for u in a_tester:
        ok, d = check_page(u)
        chemin = urllib.parse.urlparse(u).path
        if any(chemin.startswith(i.rstrip("$")) for i in interdits if i not in ("", "/")):
            ok, d["probleme"] = False, (d["probleme"] + " ; interdit par robots.txt").strip(" ;")
        print(f"  {'OK ' if ok else 'KO '} {chemin:<46} {d['titre']}")
        if not ok:
            print(f"       -> {d['probleme']}")
            ko.append((u, d["probleme"]))
        # reciprocite : la version annoncee doit renvoyer vers nous
        for code, href in list(d["alternates"].items())[:2]:
            if href == u or code == "x-default":
                continue
            ok2, d2 = check_page(href)
            if ok2 and d2["alternates"].get(_lang_of(u)) != u:
                reciprocite_ko.append((u, href))

    print()
    if ko:
        print(f"{len(ko)} page(s) non indexable(s) :")
        for u, p in ko:
            print(f"  {u}\n    {p}")
    else:
        print("Rien ne bloque : toutes les pages controlees sont indexables.")
    if reciprocite_ko:
        print(f"\n{len(reciprocite_ko)} hreflang sans reciproque :")
        for a, b in reciprocite_ko:
            print(f"  {a} -> {b} (qui ne renvoie pas)")

    print("\nCe controle ne dit pas si Google a indexe ces pages. Pour le savoir :")
    print("  Search Console > Pages (indexees / non indexees, avec le motif)")
    print("  Search Console > Inspection d'URL (colle une URL, « Demander l'indexation »)")
    return 1 if ko else 0


def _lang_of(url):
    m = re.match(r"^/(en|es|ru|hi|uk|tr|ur|bn)(?=/|$)", urllib.parse.urlparse(url).path)
    return m.group(1) if m else "fr"


if __name__ == "__main__":
    sys.exit(main(sys.argv))
