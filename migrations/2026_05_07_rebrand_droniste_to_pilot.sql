-- Migration : rebrand AubeDroniste -> AubePilot (2026-05-07)
--
-- Le rôle utilisateur 'droniste' devient 'pilot' pour s'aligner avec la
-- nouvelle marque "Aube Pilot". 'client' et 'both' sont inchangés.
--
-- À appliquer une seule fois sur chaque base existante (dev + prod) :
--   sqlite3 data/aubepilot.db < migrations/2026_05_07_rebrand_droniste_to_pilot.sql
--
-- Idempotent : un second passage ne touchera plus aucune ligne (WHERE role='droniste').
-- Pas de CHECK constraint sur la colonne role (juste un commentaire), donc
-- aucun ALTER TABLE nécessaire.

BEGIN;

UPDATE users SET role = 'pilot' WHERE role = 'droniste';

COMMIT;

-- Vérification :
--   SELECT role, COUNT(*) FROM users GROUP BY role;
-- doit retourner uniquement 'client' / 'pilot' / 'both'.
