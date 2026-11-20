"""Fil des pilotes (/fil, 2026-11-20) : un petit reseau entre pilotes.

Les pilotes publient un texte, une photo ou une video ; tout membre connecte
peut aimer et commenter. Lecture publique (comme les fiches). Les comptes
bloques (user_blocks) ne se voient plus et ne peuvent plus se commenter ;
signalement par /signaler (target_type 'post' / 'comment').
"""
from typing import Optional

import db
import services

BODY_MAX = 3000
COMMENT_MAX = 1500
PAGE = 15

TABLES = [
    """CREATE TABLE IF NOT EXISTS feed_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    author_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body            TEXT NOT NULL DEFAULT '',
    media_path      TEXT,                 -- sous UPLOAD_DIR : feed_u<id>/<horodatage>_<nom>
    media_kind      TEXT,                 -- image | video
    media_mime      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at      TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_feed_posts_created ON feed_posts(created_at)",
    """CREATE TABLE IF NOT EXISTS feed_comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         INTEGER NOT NULL REFERENCES feed_posts(id) ON DELETE CASCADE,
    author_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body            TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at      TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_feed_comments_post ON feed_comments(post_id)",
    """CREATE TABLE IF NOT EXISTS feed_likes (
    post_id     INTEGER NOT NULL REFERENCES feed_posts(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (post_id, user_id)
)""",
]


def can_post(user: Optional[dict]) -> bool:
    return bool(user) and user.get("role") in ("pilot", "both")


_AUTHOR_COLS = ("u.id AS author_id, u.full_name, u.username, u.avatar_path, u.role, "
                "u.country, u.city, u.is_verified, p.kind, p.business_name")


def _hidden_clause(viewer_id: Optional[int], col: str) -> tuple:
    """Masque les auteurs bloques dans un sens ou dans l'autre."""
    if not viewer_id:
        return "", ()
    return (f" AND {col} NOT IN (SELECT blocked_user_id FROM user_blocks WHERE blocker_user_id=?)"
            f" AND {col} NOT IN (SELECT blocker_user_id FROM user_blocks WHERE blocked_user_id=?)",
            (viewer_id, viewer_id))


def _decorate(rows: list, viewer_id: Optional[int], comments_per_post: int) -> list:
    posts = []
    for r in rows:
        d = dict(r)
        d["author_name"] = services.public_name(d) or d.get("username") or "?"
        posts.append(d)
    if not posts:
        return posts
    ids = [p["id"] for p in posts]
    marks = ",".join("?" * len(ids))
    likes = {r["post_id"]: r["n"] for r in db.fetchall(
        f"SELECT post_id, COUNT(*) AS n FROM feed_likes WHERE post_id IN ({marks}) GROUP BY post_id", ids)}
    mine = set()
    if viewer_id:
        mine = {r["post_id"] for r in db.fetchall(
            f"SELECT post_id FROM feed_likes WHERE user_id=? AND post_id IN ({marks})", [viewer_id] + ids)}
    hid, hargs = _hidden_clause(viewer_id, "c.author_user_id")
    counts = {r["post_id"]: r["n"] for r in db.fetchall(
        f"SELECT c.post_id, COUNT(*) AS n FROM feed_comments c JOIN users u ON u.id = c.author_user_id "
        f"WHERE c.deleted_at IS NULL AND u.deleted_at IS NULL AND c.post_id IN ({marks}){hid} "
        f"GROUP BY c.post_id", ids + list(hargs))}
    for p in posts:
        p["likes"] = likes.get(p["id"], 0)
        p["liked"] = p["id"] in mine
        p["comment_count"] = counts.get(p["id"], 0)
        p["comments"] = list_comments(p["id"], viewer_id, last=comments_per_post) if comments_per_post else []
    return posts


def list_posts(viewer_id: Optional[int] = None, *, before_id: Optional[int] = None,
               media_only: bool = False, author_id: Optional[int] = None,
               limit: int = PAGE, comments_per_post: int = 2) -> list:
    q = (f"SELECT f.*, {_AUTHOR_COLS} FROM feed_posts f "
         "JOIN users u ON u.id = f.author_user_id "
         "LEFT JOIN pilot_profiles p ON p.user_id = u.id "
         "WHERE f.deleted_at IS NULL AND u.deleted_at IS NULL")
    args: list = []
    if before_id:
        q += " AND f.id < ?"
        args.append(before_id)
    if media_only:
        q += " AND f.media_path IS NOT NULL"
    if author_id:
        q += " AND f.author_user_id = ?"
        args.append(author_id)
    hid, hargs = _hidden_clause(viewer_id, "f.author_user_id")
    q += hid + " ORDER BY f.id DESC LIMIT ?"
    args += list(hargs) + [limit]
    return _decorate(db.fetchall(q, args), viewer_id, comments_per_post)


def get_post(post_id: int, viewer_id: Optional[int] = None, *, comments: bool = True) -> Optional[dict]:
    hid, hargs = _hidden_clause(viewer_id, "f.author_user_id")
    row = db.fetchone(
        f"SELECT f.*, {_AUTHOR_COLS} FROM feed_posts f JOIN users u ON u.id = f.author_user_id "
        f"LEFT JOIN pilot_profiles p ON p.user_id = u.id "
        f"WHERE f.id=? AND f.deleted_at IS NULL AND u.deleted_at IS NULL{hid}", [post_id] + list(hargs))
    if not row:
        return None
    post = _decorate([row], viewer_id, 0)[0]
    if comments:
        post["comments"] = list_comments(post_id, viewer_id)
    return post


def list_comments(post_id: int, viewer_id: Optional[int] = None, last: Optional[int] = None) -> list:
    hid, hargs = _hidden_clause(viewer_id, "c.author_user_id")
    q = (f"SELECT c.*, {_AUTHOR_COLS} FROM feed_comments c JOIN users u ON u.id = c.author_user_id "
         f"LEFT JOIN pilot_profiles p ON p.user_id = u.id "
         f"WHERE c.post_id=? AND c.deleted_at IS NULL AND u.deleted_at IS NULL{hid} ")
    args = [post_id] + list(hargs)
    if last:
        q = f"SELECT * FROM ({q} ORDER BY c.id DESC LIMIT ?) ORDER BY id ASC"
        args.append(last)
    else:
        q += "ORDER BY c.id ASC"
    out = []
    for r in db.fetchall(q, args):
        d = dict(r)
        # pilotes sous leur nom complet, clients masques (« Prenom N. »)
        name = services.public_name(d) if d.get("role") in ("pilot", "both") \
            else services.mask_full_name(d.get("full_name") or "")
        d["author_name"] = name or d.get("username") or "?"
        d["author_is_pilot"] = d.get("role") in ("pilot", "both")
        out.append(d)
    return out


def create_post(author_id: int, body: str, media_path: Optional[str] = None,
                media_kind: Optional[str] = None, media_mime: Optional[str] = None) -> int:
    body = (body or "").strip()[:BODY_MAX]
    if not body and not media_path:
        raise ValueError("empty")
    cur = db.execute(
        "INSERT INTO feed_posts (author_user_id, body, media_path, media_kind, media_mime) VALUES (?, ?, ?, ?, ?)",
        (author_id, body, media_path, media_kind, media_mime))
    return int(cur.lastrowid or 0)


def raw_post(post_id: int) -> Optional[dict]:
    r = db.fetchone("SELECT * FROM feed_posts WHERE id=? AND deleted_at IS NULL", (post_id,))
    return dict(r) if r else None


def delete_post(post_id: int, user: dict) -> Optional[dict]:
    """Auteur ou admin. Retourne la publication (pour effacer son fichier)."""
    p = raw_post(post_id)
    if not p or (p["author_user_id"] != user["id"] and not user.get("is_admin")):
        return None
    db.execute("UPDATE feed_posts SET deleted_at=datetime('now') WHERE id=?", (post_id,))
    return p


def add_comment(post_id: int, author_id: int, body: str) -> int:
    body = (body or "").strip()[:COMMENT_MAX]
    if not body:
        raise ValueError("empty")
    p = raw_post(post_id)
    if not p:
        raise LookupError("post")
    if services.is_blocked_between(author_id, p["author_user_id"]):
        raise PermissionError("blocked")
    cur = db.execute("INSERT INTO feed_comments (post_id, author_user_id, body) VALUES (?, ?, ?)",
                     (post_id, author_id, body))
    return int(cur.lastrowid or 0)


def raw_comment(comment_id: int) -> Optional[dict]:
    r = db.fetchone("SELECT * FROM feed_comments WHERE id=? AND deleted_at IS NULL", (comment_id,))
    return dict(r) if r else None


def delete_comment(comment_id: int, user: dict) -> Optional[dict]:
    """Auteur du commentaire, auteur de la publication, ou admin."""
    c = raw_comment(comment_id)
    if not c:
        return None
    p = raw_post(c["post_id"]) or {}
    if user["id"] not in (c["author_user_id"], p.get("author_user_id")) and not user.get("is_admin"):
        return None
    db.execute("UPDATE feed_comments SET deleted_at=datetime('now') WHERE id=?", (comment_id,))
    return c


def toggle_like(post_id: int, user_id: int) -> tuple:
    """(aime_maintenant, total)."""
    p = raw_post(post_id)
    if not p:
        raise LookupError("post")
    if db.fetchone("SELECT 1 FROM feed_likes WHERE post_id=? AND user_id=?", (post_id, user_id)):
        db.execute("DELETE FROM feed_likes WHERE post_id=? AND user_id=?", (post_id, user_id))
        liked = False
    else:
        if services.is_blocked_between(user_id, p["author_user_id"]):
            raise PermissionError("blocked")
        db.execute("INSERT OR IGNORE INTO feed_likes (post_id, user_id) VALUES (?, ?)", (post_id, user_id))
        liked = True
    row = db.fetchone("SELECT COUNT(*) AS n FROM feed_likes WHERE post_id=?", (post_id,))
    return liked, (row["n"] if row else 0)
