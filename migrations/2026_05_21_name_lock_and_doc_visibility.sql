-- Verrouillage du nom apres upload d'un brevet
-- Demande de changement de nom : justif obligatoire, validation admin

CREATE TABLE IF NOT EXISTS name_change_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    current_name    TEXT NOT NULL,
    requested_name  TEXT NOT NULL,
    reason          TEXT,
    justif_path     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    reviewed_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at     TEXT,
    admin_note      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_name_change_user ON name_change_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_name_change_status ON name_change_requests(status);
