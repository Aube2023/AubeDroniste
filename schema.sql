-- AubePilot - schema SQLite
-- Marketplace pilotes <-> clients

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Comptes (un compte peut etre client, pilote ou les deux)
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,           -- compte PAM
    email         TEXT NOT NULL UNIQUE,           -- @aubemail.com
    full_name     TEXT NOT NULL,
    phone         TEXT,
    country       TEXT,
    city          TEXT,
    lat           REAL,
    lng           REAL,
    role          TEXT NOT NULL DEFAULT 'client', -- 'client' | 'pilot' | 'both'
    avatar_path   TEXT,
    bio           TEXT,
    is_verified   INTEGER NOT NULL DEFAULT 0,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_country ON users(country);
CREATE INDEX IF NOT EXISTS idx_users_role    ON users(role);

-- Profil pilote etendu
CREATE TABLE IF NOT EXISTS pilot_profiles (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    headline          TEXT,                       -- accroche courte
    business_name     TEXT,                       -- marque / raison sociale (mise en avant sur le devis)
    years_experience  INTEGER NOT NULL DEFAULT 0,
    hourly_rate       REAL,                       -- tarif horaire
    daily_rate        REAL,                       -- tarif journee
    currency          TEXT NOT NULL DEFAULT 'EUR',
    travel_radius_km  INTEGER NOT NULL DEFAULT 50,
    accepts_remote    INTEGER NOT NULL DEFAULT 0, -- accepte missions hors zone
    insurance         INTEGER NOT NULL DEFAULT 0, -- assure RC pro
    insurance_company TEXT,
    insurance_policy  TEXT,
    is_available      INTEGER NOT NULL DEFAULT 1,
    languages         TEXT,                       -- "fr,ar,en"
    portfolio_url     TEXT,
    accepts_urgent    INTEGER NOT NULL DEFAULT 0,
    -- Stripe Connect Express (rempli a l'onboarding)
    stripe_account_id      TEXT,                  -- "acct_..." ou "acct_fake_..."
    stripe_charges_enabled INTEGER NOT NULL DEFAULT 0,
    stripe_payouts_enabled INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Formations / certifications du pilote
CREATE TABLE IF NOT EXISTS pilot_certifications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    authority      TEXT NOT NULL,                 -- DGAC / EASA / TC / FAA ...
    title          TEXT NOT NULL,                 -- "STS-01", "Part 107", "Avance"
    reference      TEXT,                          -- numero
    issued_at      TEXT,
    expires_at     TEXT,
    document_path  TEXT,                          -- copie scan
    is_verified    INTEGER NOT NULL DEFAULT 0,    -- valide par l'admin
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cert_pilot ON pilot_certifications(pilot_user_id);

-- Drones du pilote
CREATE TABLE IF NOT EXISTS pilot_drones (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category       TEXT NOT NULL,                 -- micro/loisir/pro_camera/...
    brand          TEXT,
    model          TEXT,
    serial_number  TEXT,
    weight_g       INTEGER,
    max_payload_g  INTEGER,
    flight_time_min INTEGER,
    capabilities   TEXT,                          -- CSV: "camera_4k,thermique,..."
    photo_path     TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_drone_pilot ON pilot_drones(pilot_user_id);

-- Specialites du pilote (repete les codes MISSION_TYPES)
CREATE TABLE IF NOT EXISTS pilot_specialties (
    pilot_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mission_type   TEXT NOT NULL,
    PRIMARY KEY (pilot_user_id, mission_type)
);

-- Pays autorises a operer (un pilote peut etre licencie dans plusieurs pays)
CREATE TABLE IF NOT EXISTS pilot_territories (
    pilot_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    country        TEXT NOT NULL,
    region         TEXT,
    PRIMARY KEY (pilot_user_id, country, region)
);

-- Missions publiees par les clients
CREATE TABLE IF NOT EXISTS missions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    mission_type    TEXT NOT NULL,
    country         TEXT NOT NULL,
    region          TEXT,
    city            TEXT,
    lat             REAL,
    lng             REAL,
    address         TEXT,
    budget_min      REAL,
    budget_max      REAL,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    duration_hours  REAL,
    start_date      TEXT,
    end_date        TEXT,
    is_urgent       INTEGER NOT NULL DEFAULT 0,
    requires_insurance INTEGER NOT NULL DEFAULT 0,
    requires_certifications TEXT,                 -- CSV codes
    requires_capabilities   TEXT,                 -- CSV (thermique, RTK, ...)
    status          TEXT NOT NULL DEFAULT 'open',
    from_package_id   INTEGER REFERENCES pilot_packages(id) ON DELETE SET NULL,
    targeted_pilot_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mission_country ON missions(country);
CREATE INDEX IF NOT EXISTS idx_mission_type    ON missions(mission_type);
CREATE INDEX IF NOT EXISTS idx_mission_status  ON missions(status);
CREATE INDEX IF NOT EXISTS idx_mission_geo     ON missions(lat, lng);

-- Devis d'un pilote sur une mission (anciennement "enchere")
-- Cycle : pending -> accepted (client valide) | rejected (client refuse,
-- le pilote peut alors reviser et resoumettre, revision_no++) | withdrawn
CREATE TABLE IF NOT EXISTS bids (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id      INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    pilot_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    price           REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    eta_hours       REAL,
    message         TEXT,                          -- mot d'accompagnement
    description     TEXT,                          -- description detaillee du devis
    deliverables    TEXT,                          -- livrables (photos RAW, plan PDF...)
    terms           TEXT,                          -- conditions / details techniques
    revision_no     INTEGER NOT NULL DEFAULT 1,    -- numero de revision
    client_response TEXT,                          -- raison de refus client
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (mission_id, pilot_user_id)
);
CREATE INDEX IF NOT EXISTS idx_bid_mission ON bids(mission_id);
CREATE INDEX IF NOT EXISTS idx_bid_pilot   ON bids(pilot_user_id);

-- Historique des revisions de devis : on snapshote l'ancienne version
-- au moment ou le pilote en soumet une nouvelle (apres refus client).
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
    status          TEXT NOT NULL,
    client_response TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bid_rev_bid ON bid_revisions(bid_id);

-- Reservation issue d'une enchere acceptee
CREATE TABLE IF NOT EXISTS bookings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id      INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    bid_id          INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    client_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pilot_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agreed_price    REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    platform_fee    REAL NOT NULL DEFAULT 0,
    scheduled_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending_payment',
    completed_at    TEXT,
    -- Stripe escrow trace
    stripe_payment_intent_id TEXT,
    stripe_session_id        TEXT,
    stripe_transfer_id       TEXT,
    paid_at                  TEXT,    -- date de capture du paiement
    released_at              TEXT,    -- date de transfer au pilote
    refunded_at              TEXT,
    dispute_reason           TEXT,
    -- Annulation tardive : si le client annule a moins de
    -- LATE_CANCELLATION_HOURS de la mission, une fraction du devis
    -- (LATE_CANCELLATION_FEE_PCT) est versee au pilote a titre de
    -- dedommagement et le client est rembourse du reste.
    cancelled_at             TEXT,
    cancellation_fee         REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_booking_pilot  ON bookings(pilot_user_id);
CREATE INDEX IF NOT EXISTS idx_booking_client ON bookings(client_user_id);

-- Avis bidirectionnels
CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id      INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    author_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating          INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (booking_id, author_user_id)
);
CREATE INDEX IF NOT EXISTS idx_review_target ON reviews(target_user_id);

-- Messagerie simple par mission (client <-> pilote)
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id      INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    sender_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body            TEXT NOT NULL,
    read_at         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_msg_mission ON messages(mission_id);
CREATE INDEX IF NOT EXISTS idx_msg_recipient ON messages(recipient_user_id);

-- Forfaits proposes par le pilote (catalogue de packages)
CREATE TABLE IF NOT EXISTS pilot_packages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    mission_type    TEXT,
    price           REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    duration_hours  REAL,
    deliverables    TEXT,
    capabilities    TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pkg_pilot ON pilot_packages(pilot_user_id);
CREATE INDEX IF NOT EXISTS idx_pkg_type  ON pilot_packages(mission_type);

-- Portfolio pilote : galerie photos + videos d'operations passees
-- (showreel commercial sur la page publique).
CREATE TABLE IF NOT EXISTS pilot_portfolio_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               TEXT,
    description         TEXT,
    kind                TEXT NOT NULL DEFAULT 'image',
    original_filename   TEXT NOT NULL,
    stored_filename     TEXT NOT NULL,
    mime_type           TEXT,
    size_bytes          INTEGER NOT NULL DEFAULT 0,
    thumb_filename      TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_portfolio_pilot ON pilot_portfolio_items(pilot_user_id);

-- Livrables d'une booking (photos / videos / ortho / PDFs / ZIPs uploades
-- par le pilote, telechargeables par le client, pushables vers AubeDrive
-- ou AubePhotos)
CREATE TABLE IF NOT EXISTS booking_deliverables (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id            INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    uploaded_by_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    label                 TEXT,
    original_filename     TEXT NOT NULL,
    stored_filename       TEXT NOT NULL,
    mime_type             TEXT,
    size_bytes            INTEGER NOT NULL DEFAULT 0,
    kind                  TEXT NOT NULL DEFAULT 'file',
    aubedrive_url         TEXT,
    aubedrive_sent_at     TEXT,
    aubephotos_url        TEXT,
    aubephotos_sent_at    TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_deliv_booking ON booking_deliverables(booking_id);

-- Sessions
CREATE TABLE IF NOT EXISTS sessions (
    sid          TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL,
    user_agent   TEXT,
    ip           TEXT
);
CREATE INDEX IF NOT EXISTS idx_sess_user ON sessions(user_id);

-- Audit minimal
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    payload     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Demandes de changement de nom (verrouillage apres upload brevet)
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
