-- 2026-05-24 : portfolio pilote (galerie photos + videos d'operations
-- passees). Le pilote met en avant son travail sur sa page publique.

PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS pilot_portfolio_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               TEXT,
    description         TEXT,
    kind                TEXT NOT NULL DEFAULT 'image',  -- image | video
    original_filename   TEXT NOT NULL,
    stored_filename     TEXT NOT NULL,
    mime_type           TEXT,
    size_bytes          INTEGER NOT NULL DEFAULT 0,
    -- Vignette generee pour les videos (frame extraite) ou la version
    -- carre de l'image pour la galerie. NULL = on affiche le fichier
    -- directement.
    thumb_filename      TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_portfolio_pilot ON pilot_portfolio_items(pilot_user_id);
