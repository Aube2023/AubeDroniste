-- 2026-05-23 : forfaits pilote + livrables booking + integrations Aube

PRAGMA foreign_keys=ON;

-- Forfaits proposes par le pilote (packages catalogue).
-- Le client peut commander un forfait directement (raccourci par rapport
-- a la negociation devis-libre via mission ouverte).
CREATE TABLE IF NOT EXISTS pilot_packages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    mission_type    TEXT,                          -- code MISSION_TYPES ou NULL = general
    price           REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    duration_hours  REAL,                          -- duree estimee
    deliverables    TEXT,                          -- livrables inclus (texte libre)
    capabilities    TEXT,                          -- CSV: camera_4k,thermique,RTK...
    is_active       INTEGER NOT NULL DEFAULT 1,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pkg_pilot ON pilot_packages(pilot_user_id);
CREATE INDEX IF NOT EXISTS idx_pkg_type  ON pilot_packages(mission_type);

-- Trace : quelle mission a ete creee a partir d'un forfait
ALTER TABLE missions ADD COLUMN from_package_id    INTEGER REFERENCES pilot_packages(id) ON DELETE SET NULL;
ALTER TABLE missions ADD COLUMN targeted_pilot_id  INTEGER REFERENCES users(id) ON DELETE SET NULL;

-- Livrables d'une booking : photos, videos, ortho, PDFs, archives ZIP
-- uploades par le pilote a la fin de mission. Le client telecharge,
-- puis peut pousser vers AubeDrive/AubePhotos.
CREATE TABLE IF NOT EXISTS booking_deliverables (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id            INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    uploaded_by_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    label                 TEXT,                    -- titre court
    original_filename     TEXT NOT NULL,
    stored_filename       TEXT NOT NULL,           -- nom sur disque (deduplique)
    mime_type             TEXT,
    size_bytes            INTEGER NOT NULL DEFAULT 0,
    kind                  TEXT NOT NULL DEFAULT 'file',  -- file | image | video | archive | doc
    -- Push vers d'autres services Aube
    aubedrive_url         TEXT,
    aubedrive_sent_at     TEXT,
    aubephotos_url        TEXT,
    aubephotos_sent_at    TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_deliv_booking ON booking_deliverables(booking_id);
