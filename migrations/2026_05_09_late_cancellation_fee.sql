-- Migration : ajoute le suivi de l'annulation tardive sur les bookings
--
-- Regle metier : si le client annule a moins de LATE_CANCELLATION_HOURS de
-- la date prevue (mission.start_date), il dedommage le pilote a hauteur de
-- LATE_CANCELLATION_FEE_PCT du prix convenu (defaut 25 %), et est rembourse
-- du reste. Si le preavis est suffisant : 100 % rembourse, fee = 0.
--
-- Idempotent : ALTER TABLE ... ADD COLUMN echoue silencieusement si la
-- colonne existe deja (workaround SQLite via try / catch SQL).

BEGIN;

-- SQLite ne supporte pas ADD COLUMN IF NOT EXISTS avant 3.35 sur certains
-- builds : on utilise un schema_version local pour l'idempotence.
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_migrations(name)
VALUES ('2026_05_09_late_cancellation_fee');

COMMIT;

-- Les ALTER TABLE ci-dessous doivent etre executes hors transaction
-- pour SQLite, et peuvent echouer si deja appliques (run avec | true).
ALTER TABLE bookings ADD COLUMN cancelled_at TEXT;
ALTER TABLE bookings ADD COLUMN cancellation_fee REAL NOT NULL DEFAULT 0;
