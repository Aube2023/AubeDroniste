-- 2026-05-22 : devis enrichi + cycle valider/refuser/reviser
-- Le pilote envoie un devis avec description + livrables + conditions
-- techniques. Le client peut discuter, valider ou refuser. Si refus,
-- le pilote revise et resoumet un nouveau devis (revision_no++); l'ancien
-- est archive dans bid_revisions pour garder l'historique.

PRAGMA foreign_keys=ON;

ALTER TABLE bids ADD COLUMN description    TEXT;
ALTER TABLE bids ADD COLUMN deliverables   TEXT;
ALTER TABLE bids ADD COLUMN terms          TEXT;
ALTER TABLE bids ADD COLUMN revision_no    INTEGER NOT NULL DEFAULT 1;
ALTER TABLE bids ADD COLUMN client_response TEXT;
ALTER TABLE bids ADD COLUMN updated_at     TEXT NOT NULL DEFAULT (datetime('now'));

CREATE TABLE IF NOT EXISTS bid_revisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id          INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    revision_no     INTEGER NOT NULL,
    price           REAL NOT NULL,
    currency        TEXT NOT NULL,
    eta_hours       REAL,
    message         TEXT,
    description     TEXT,
    deliverables    TEXT,
    terms           TEXT,
    status          TEXT NOT NULL,           -- snapshot du statut avant revision
    client_response TEXT,                    -- raison du refus client si applicable
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bid_rev_bid ON bid_revisions(bid_id);
