"""Offres d'emploi déposées sur AubePilot par les entreprises et les écoles.

Gratuit, avec le logo de l'entreprise, et vérifié AVANT la mise en ligne.
Ces offres vivent dans la table `opportunities` (kind='job',
source='aubepilot') : elles profitent de la liste /emplois (filtres pays,
province et spécialité), des compteurs, du courriel du lundi et de
l'expiration nocturne (opportunities.expire) sans code en double.

Ce qui leur est propre :
- statut 'pending' tant qu'un administrateur ne les a pas validées
  (/admin/opportunites) ; toute modification les y renvoie, pour qu'une offre
  validée ne puisse pas devenir autre chose en silence ;
- une fiche complète servie par AubePilot (/emplois/<id>), avec les données
  structurées JobPosting lues par Google pour l'emploi ;
- le logo est celui du COMPTE (users.org_logo_path), repris sur toutes ses
  offres et servi par /media (motif orglogo_u<id>_*). Son type est lu dans
  le fichier lui-même : un SVG ou une page HTML renommés en .png sont refusés.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

import db
import opportunities as opp
from config import COUNTRIES, UPLOAD_DIR

SOURCE = "aubepilot"
EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "internship", "seasonal", "freelance")
# schema.org JobPosting.employmentType
SCHEMA_EMPLOYMENT = {"full_time": "FULL_TIME", "part_time": "PART_TIME", "contract": "CONTRACTOR",
                     "internship": "INTERN", "seasonal": "TEMPORARY", "freelance": "CONTRACTOR"}
DEFAULT_DAYS = 60      # sans date de fin choisie, l'offre reste en ligne 60 jours
MAX_DAYS = 90          # date de fin au plus loin
MAX_ACTIVE = 20        # offres en vérification ou en ligne par compte
TITLE_MIN, TITLE_MAX = 5, 140
BODY_MIN, BODY_MAX = 80, 6000
MAX_LOGO_MB = 2
FIELDS = ("title", "org", "country", "region", "city", "employment_type", "salary",
          "description", "apply_url", "apply_email", "closes_at")


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _line(value, limit: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def _parse_date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def date_bounds() -> tuple:
    """Bornes du champ « fin de publication » (aujourd'hui, + MAX_DAYS)."""
    today = date.today()
    return today.isoformat(), (today + timedelta(days=MAX_DAYS)).isoformat()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def clean_url(value: str) -> str:
    """Lien http(s) vers la page de candidature ; « www.exemple.com/emplois »
    reçoit https://. Tout autre schéma (javascript:, data:…) est refusé."""
    v = (value or "").strip()
    if not v:
        return ""
    if not re.match(r"^https?://", v, re.I):
        if re.match(r"^[a-z][a-z0-9+.-]*:", v, re.I):
            return ""
        v = "https://" + v
    if len(v) > 500 or re.search(r"\s", v):
        return ""
    try:
        host = urlsplit(v).hostname or ""
    except ValueError:
        return ""
    return v if "." in host else ""


_EMAIL = re.compile(r"^[^@\s<>\"'(),;:]+@[^@\s<>\"'(),;:]+\.[A-Za-z]{2,}$")


def clean_email(value: str) -> str:
    v = (value or "").strip()[:200]
    return v if _EMAIL.match(v) else ""


def validate(form) -> tuple:
    """(champs nettoyés, erreurs). Chaque erreur est une clé de traduction
    job.err_* : la route l'affiche dans la langue de la page."""
    raw = {k: (form.get(k) or "") for k in FIELDS}
    out = {
        "title": _line(raw["title"], TITLE_MAX),
        "org": _line(raw["org"], 120),
        "country": raw["country"].strip(),
        "region": _line(raw["region"], 80),
        "city": _line(raw["city"], 80),
        "employment_type": raw["employment_type"].strip(),
        "salary": _line(raw["salary"], 80),
        "description": raw["description"].strip()[:BODY_MAX],
        "apply_url": clean_url(raw["apply_url"]),
        "apply_email": clean_email(raw["apply_email"]),
        "closes_at": "",
    }
    errors = []
    if len(out["title"]) < TITLE_MIN:
        errors.append("job.err_title")
    if len(out["org"]) < 2:
        errors.append("job.err_org")
    if out["country"] not in COUNTRIES:
        errors.append("job.err_country")
    if out["employment_type"] not in EMPLOYMENT_TYPES:
        out["employment_type"] = EMPLOYMENT_TYPES[0]
    if len(out["description"]) < BODY_MIN:
        errors.append("job.err_description")
    typed_url, typed_email = raw["apply_url"].strip(), raw["apply_email"].strip()
    if ((typed_url and not out["apply_url"]) or (typed_email and not out["apply_email"])
            or not (out["apply_url"] or out["apply_email"])):
        errors.append("job.err_apply")
    today = date.today()
    if raw["closes_at"].strip():
        end = _parse_date(raw["closes_at"])
        if not end or end < today or end > today + timedelta(days=MAX_DAYS):
            errors.append("job.err_closes")
        else:
            out["closes_at"] = end.isoformat()
    else:
        out["closes_at"] = (today + timedelta(days=DEFAULT_DAYS)).isoformat()
    return out, errors


def _region(fields: dict) -> str:
    """Au Canada, la province de référence des autres fiches (« AB » ->
    « Alberta ») pour que le filtre par province les réunisse ; ailleurs, ce
    qui a été saisi, ou le pays."""
    if fields.get("country") == "Canada":
        return opp.normalize_region(fields.get("region") or fields.get("city") or "")[0]
    return fields.get("region") or fields.get("country") or ""


def _values(fields: dict) -> dict:
    return {
        "title": fields["title"], "summary": opp.summarize(fields["description"]),
        "org": fields["org"], "country": fields["country"], "region": _region(fields),
        "regions_raw": fields["region"], "city": fields["city"],
        "specialties": opp.specialties_for(fields["title"] + " " + fields["description"]),
        "closes_at": fields["closes_at"], "body": fields["description"],
        "apply_url": fields["apply_url"], "apply_email": fields["apply_email"],
        "employment_type": fields["employment_type"], "salary": fields["salary"],
    }


def _urls(opp_id: int) -> tuple:
    import seo
    return seo.CANONICAL_BASE + f"/emplois/{opp_id}", seo.CANONICAL_BASE + f"/en/emplois/{opp_id}"


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------

def create(user_id: int, fields: dict, *, publish: bool = False) -> int:
    """Nouvelle offre, en vérification ; `publish` (administrateur) la met
    en ligne tout de suite. Retourne son identifiant."""
    v = _values(fields)
    now = _now()
    cur = db.execute(
        "INSERT INTO opportunities (source, source_ref, kind, title_fr, title_en, summary_fr, summary_en, org, "
        "country, region, regions_raw, city, url_fr, url_en, notice_type, category, specialties, published_at, "
        "closes_at, status, first_seen_at, last_seen_at, posted_by, body, apply_url, apply_email, "
        "employment_type, salary, review_note) "
        "VALUES (?, ?, 'job', ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '', 'job', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
        (SOURCE, f"u{int(user_id)}-{secrets.token_hex(5)}", v["title"], v["title"], v["summary"], v["summary"],
         v["org"], v["country"], v["region"], v["regions_raw"], v["city"], v["specialties"],
         date.today().isoformat() if publish else None, v["closes_at"], "published" if publish else "pending",
         now, now, user_id, v["body"], v["apply_url"], v["apply_email"], v["employment_type"], v["salary"]),
    )
    opp_id = int(cur.lastrowid or 0)
    url_fr, url_en = _urls(opp_id)
    db.execute("UPDATE opportunities SET url_fr=?, url_en=? WHERE id=?", (url_fr, url_en, opp_id))
    return opp_id


def update(opp_id: int, user_id: int, fields: dict, *, publish: bool = False) -> bool:
    """Modification par son auteur : l'offre repasse en vérification (une
    offre retirée par l'équipe, elle, ne se modifie plus)."""
    v = _values(fields)
    status = "published" if publish else "pending"
    cur = db.execute(
        "UPDATE opportunities SET title_fr=?, title_en=?, summary_fr=?, summary_en=?, org=?, country=?, region=?, "
        "regions_raw=?, city=?, specialties=?, closes_at=?, body=?, apply_url=?, apply_email=?, employment_type=?, "
        "salary=?, status=?, review_note='', last_seen_at=?, "
        "published_at=CASE WHEN ?='published' THEN COALESCE(published_at, date('now')) ELSE published_at END "
        "WHERE id=? AND source=? AND posted_by=? AND status != 'hidden'",
        (v["title"], v["title"], v["summary"], v["summary"], v["org"], v["country"], v["region"],
         v["regions_raw"], v["city"], v["specialties"], v["closes_at"], v["body"], v["apply_url"],
         v["apply_email"], v["employment_type"], v["salary"], status, _now(), status,
         opp_id, SOURCE, user_id),
    )
    return cur.rowcount > 0


def close(opp_id: int, user_id: int) -> bool:
    cur = db.execute(
        "UPDATE opportunities SET status='closed' WHERE id=? AND source=? AND posted_by=? "
        "AND status IN ('pending', 'published')", (opp_id, SOURCE, user_id))
    return cur.rowcount > 0


def delete(opp_id: int, user_id: int) -> bool:
    """Supprime une offre de son auteur. Une offre retirée par l'équipe
    (signalement) reste en base pour la modération."""
    cur = db.execute(
        "DELETE FROM opportunities WHERE id=? AND source=? AND posted_by=? AND status != 'hidden'",
        (opp_id, SOURCE, user_id))
    return cur.rowcount > 0


def _audit(user_id: int, action: str, opp_id: int, note: str = "") -> None:
    db.execute("INSERT INTO audit_log (user_id, action, target, payload) VALUES (?, ?, ?, ?)",
               (user_id, action, f"opportunity:{opp_id}", json.dumps({"note": note} if note else {})))


def approve(opp_id: int, admin_id: int) -> Optional[dict]:
    """Mise en ligne par un administrateur. La première publication date
    l'offre et la fait entrer dans le courriel du lundi (first_seen_at) ;
    une date de fin dépassée pendant l'attente est repoussée."""
    post = get(opp_id)
    if not post or post["status"] != "pending":
        return None
    today = date.today()
    end = _parse_date(post.get("closes_at"))
    if not end or end < today:
        end = today + timedelta(days=DEFAULT_DAYS)
    db.execute(
        "UPDATE opportunities SET status='published', review_note='', closes_at=?, "
        "first_seen_at=CASE WHEN published_at IS NULL THEN ? ELSE first_seen_at END, "
        "published_at=COALESCE(published_at, ?) WHERE id=? AND source=? AND status='pending'",
        (end.isoformat(), _now(), today.isoformat(), opp_id, SOURCE))
    _audit(admin_id, "job_post_approved", opp_id)
    return get(opp_id)


def reject(opp_id: int, admin_id: int, note: str) -> Optional[dict]:
    """Refus motivé : l'auteur voit le motif et peut corriger son offre."""
    post = get(opp_id)
    if not post or post["status"] != "pending":
        return None
    note = (note or "").strip()[:500]
    db.execute("UPDATE opportunities SET status='rejected', review_note=? WHERE id=? AND source=? AND status='pending'",
               (note, opp_id, SOURCE))
    _audit(admin_id, "job_post_rejected", opp_id, note)
    return get(opp_id)


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def get(opp_id: int) -> Optional[dict]:
    row = db.fetchone("SELECT * FROM opportunities WHERE id=? AND source=?", (opp_id, SOURCE))
    return dict(row) if row else None


def is_public(post: Optional[dict]) -> bool:
    return bool(post) and post.get("status") == "published" and (
        not post.get("closes_at") or post["closes_at"] >= date.today().isoformat())


def list_for_user(user_id: int) -> list:
    """Offres d'un compte, les plus récentes d'abord. Une offre en ligne dont
    la date de fin est passée se lit « fermée » (la nuit la fermera)."""
    today = date.today().isoformat()
    out = []
    for r in db.fetchall("SELECT * FROM opportunities WHERE source=? AND posted_by=? ORDER BY id DESC",
                         (SOURCE, user_id)):
        d = dict(r)
        if d["status"] == "published" and d.get("closes_at") and d["closes_at"] < today:
            d["status"] = "closed"
        out.append(d)
    return out


def count_active(user_id: int) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM opportunities WHERE source=? AND posted_by=? "
        "AND status IN ('pending', 'published') AND (closes_at IS NULL OR closes_at >= date('now'))",
        (SOURCE, user_id))
    return int(row["n"]) if row else 0


def list_pending() -> list:
    rows = db.fetchall(
        "SELECT o.*, u.username AS poster_username, u.email AS poster_email, u.org_logo_path AS logo_path "
        "FROM opportunities o LEFT JOIN users u ON u.id = o.posted_by "
        "WHERE o.source=? AND o.status='pending' ORDER BY o.id", (SOURCE,))
    return [dict(r) for r in rows]


def count_pending() -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM opportunities WHERE source=? AND status='pending'", (SOURCE,))
    return int(row["n"]) if row else 0


def sitemap_posts() -> list:
    return [dict(r) for r in db.fetchall(
        "SELECT id, last_seen_at AS lastmod FROM opportunities WHERE source=? AND status='published' "
        "AND (closes_at IS NULL OR closes_at >= date('now')) ORDER BY id", (SOURCE,))]


def form_values(post: dict) -> dict:
    """Le formulaire d'édition rempli avec une offre enregistrée."""
    return {"title": post.get("title_fr") or "", "org": post.get("org") or "", "country": post.get("country") or "",
            "region": post.get("regions_raw") or "", "city": post.get("city") or "",
            "employment_type": post.get("employment_type") or "", "salary": post.get("salary") or "",
            "description": post.get("body") or "", "apply_url": post.get("apply_url") or "",
            "apply_email": post.get("apply_email") or "", "closes_at": post.get("closes_at") or ""}


def default_org(user_id: int) -> str:
    """Le nom d'employeur de la dernière offre du compte, sinon sa raison
    sociale de profil, sinon son nom."""
    row = db.fetchone("SELECT org FROM opportunities WHERE source=? AND posted_by=? ORDER BY id DESC LIMIT 1",
                      (SOURCE, user_id))
    if row and row["org"]:
        return row["org"]
    row = db.fetchone("SELECT u.full_name, p.business_name FROM users u "
                      "LEFT JOIN pilot_profiles p ON p.user_id = u.id WHERE u.id=?", (user_id,))
    return ((row["business_name"] or row["full_name"] or "").strip()) if row else ""


# ---------------------------------------------------------------------------
# Logo de l'entreprise (rattaché au compte)
# ---------------------------------------------------------------------------

def logo_path(user_id: int) -> str:
    row = db.fetchone("SELECT org_logo_path FROM users WHERE id=?", (user_id,))
    return (row["org_logo_path"] or "") if row else ""


def _media(rel: str) -> str:
    return "/media/" + rel[len("uploads/"):] if (rel or "").startswith("uploads/") else ""


def logo_url(user_id: int) -> str:
    return _media(logo_path(user_id)) if user_id else ""


def logos_for(user_ids) -> dict:
    """{id de compte: URL du logo} pour une liste d'offres (une requête)."""
    ids = sorted({int(i) for i in user_ids if i})
    if not ids:
        return {}
    rows = db.fetchall(f"SELECT id, org_logo_path FROM users WHERE id IN ({','.join('?' * len(ids))})", ids)
    return {r["id"]: _media(r["org_logo_path"]) for r in rows if _media(r["org_logo_path"])}


def _image_kind(head: bytes) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return ""


def save_logo(file, user_id: int) -> tuple:
    """Enregistre le logo envoyé. Retourne (chemin 'uploads/…' ou '', clé
    d'erreur ou ''). Rien d'envoyé : ('', '')."""
    if not file or not getattr(file, "filename", ""):
        return "", ""
    head = file.stream.read(16)
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    kind = _image_kind(head)
    if not kind or size > MAX_LOGO_MB * 1024 * 1024:
        return "", "job.err_logo"
    name = f"orglogo_u{int(user_id)}_{int(time.time())}_{secrets.token_hex(3)}.{kind}"
    file.save(os.path.join(UPLOAD_DIR, name))
    return f"uploads/{name}", ""


def set_logo(user_id: int, rel: Optional[str]) -> str:
    """Pose (ou retire, rel=None) le logo du compte ; retourne l'ancien
    chemin, à effacer du disque par l'appelant."""
    old = logo_path(user_id)
    db.execute("UPDATE users SET org_logo_path=? WHERE id=?", (rel or None, user_id))
    return old


def clear_logo(user_id: int) -> str:
    return set_logo(user_id, None)


# ---------------------------------------------------------------------------
# Données structurées
# ---------------------------------------------------------------------------

_ISO2: dict = {}


def country_iso2(name: str) -> str:
    """'Canada' -> 'CA' (addressCountry du JSON-LD) ; '' si inconnu."""
    if not _ISO2:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "geodata", "country_iso2.json"),
                      encoding="utf-8") as f:
                _ISO2.update({v: k for k, v in json.load(f).items() if len(k) == 2 and k.isalpha()})
        except (OSError, ValueError):
            pass
    return _ISO2.get(name or "", "")
