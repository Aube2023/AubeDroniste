"""Tables AubeBeacon (télémétrie des balises de drone).

Source unique du DDL : `db._ADD_TABLES` reprend cette liste (migrations
additives rejouées à chaque démarrage, donc la prod existante reçoit les
tables) et `schema.sql` en garde une copie commentée pour une base neuve.
Un test (`tests/test_beacon.py::test_schema_sql_a_jour`) vérifie que les
deux ne divergent pas.

Choix :
- SQLite reste la base (celle d'AubePilot). Une heure de vol à 0,5 Hz fait
  1 800 lignes ; les deux index couvrent les lectures chaudes (dernier point
  d'une balise, trace d'un vol). PostGIS / Timescale sont documentés dans
  AubeBeacon/server/database/README.md comme étape 2, pas en V1.
- `UNIQUE(device_id, packet_id)` rend l'ingestion idempotente : une balise
  qui rejoue sa file hors connexion ne crée jamais de doublon.
- Le jeton d'une balise n'est stocké que haché (cf. beacon/devices.py).
"""

TABLES = [
    # Balises : identité, propriétaire, drone associé, jeton haché, dernier signe de vie.
    """CREATE TABLE IF NOT EXISTS beacon_devices (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    device_uid        TEXT NOT NULL UNIQUE,            -- AUBE-BCN-000001 (imprimé sur la balise)
    owner_user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    drone_id          INTEGER REFERENCES pilot_drones(id) ON DELETE SET NULL,
    label             TEXT,                            -- nom donné par le pilote
    token_hash        TEXT NOT NULL,                   -- HMAC-SHA256 du jeton, jamais le jeton
    token_rotated_at  TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1,      -- 0 = désactivée : toute télémétrie refusée
    firmware_version  TEXT,
    last_seen_at      TEXT,                            -- dernier paquet accepté (ISO 8601 UTC)
    last_ip           TEXT,
    last_state        TEXT,                            -- dernier état de vol annoncé
    last_telemetry_id INTEGER,                         -- dernier point positionné (position connue)
    last_battery      INTEGER,
    last_signal_dbm   INTEGER,
    last_network_type TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_beacon_devices_owner ON beacon_devices(owner_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_beacon_devices_drone ON beacon_devices(drone_id)",
    # Sessions de vol : ouvertes au décollage annoncé, closes à l'atterrissage,
    # abandonnées après un long silence (cf. beacon/flights.py).
    """CREATE TABLE IF NOT EXISTS beacon_flights (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id               INTEGER NOT NULL REFERENCES beacon_devices(id) ON DELETE CASCADE,
    drone_id                INTEGER REFERENCES pilot_drones(id) ON DELETE SET NULL,
    owner_user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status                  TEXT NOT NULL DEFAULT 'PREPARING', -- PREPARING | ACTIVE | FINISHED | ABORTED
    takeoff_at              TEXT,
    landing_at              TEXT,
    duration_s              INTEGER,
    distance_m              REAL,
    max_speed_mps           REAL,
    max_relative_altitude_m REAL,
    points                  INTEGER NOT NULL DEFAULT 0,
    last_lat                REAL,
    last_lng                REAL,
    last_point_at           TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_beacon_flights_device ON beacon_flights(device_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_beacon_flights_owner ON beacon_flights(owner_user_id, takeoff_at)",
    # Points de télémétrie : un par paquet positionné. Les paquets sans position
    # (battement de cœur, GPS perdu) ne mettent à jour que la balise.
    """CREATE TABLE IF NOT EXISTS beacon_telemetry (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id            INTEGER NOT NULL REFERENCES beacon_devices(id) ON DELETE CASCADE,
    flight_id            INTEGER REFERENCES beacon_flights(id) ON DELETE SET NULL,
    packet_id            TEXT NOT NULL,                -- identifiant posé par la balise (idempotence)
    device_timestamp     TEXT,                         -- horloge de la balise, NULL avant le premier fix
    server_timestamp     TEXT NOT NULL,                -- réception serveur (ISO 8601 UTC)
    latitude             REAL NOT NULL,
    longitude            REAL NOT NULL,
    gnss_altitude_m      REAL,                         -- ellipsoïde/MSL du récepteur : PAS une hauteur sol
    barometric_altitude_m REAL,
    relative_altitude_m  REAL,                         -- par rapport au point de décollage (baro calibré au sol)
    speed_mps            REAL,
    heading_deg          REAL,
    satellites           INTEGER,
    gnss_fix             INTEGER NOT NULL DEFAULT 0,
    network_type         TEXT,                         -- LTE-M | NB-IoT | ...
    network_signal_dbm   INTEGER,
    battery_percent      INTEGER,
    flight_state         TEXT NOT NULL,
    flags                TEXT,                         -- anomalies : jump (saut impossible), late (rejeu)
    UNIQUE(device_id, packet_id)
)""",
    "CREATE INDEX IF NOT EXISTS idx_beacon_telemetry_device_time ON beacon_telemetry(device_id, server_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_beacon_telemetry_flight ON beacon_telemetry(flight_id, id)",
    # Journal des balises : création, rotation de jeton, désactivation, échecs
    # d'authentification, première connexion. Jamais le jeton.
    """CREATE TABLE IF NOT EXISTS beacon_device_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  INTEGER REFERENCES beacon_devices(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,      -- created | token_rotated | enabled | disabled | attached | detached | auth_failed | first_seen | deleted
    detail     TEXT,
    ip         TEXT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    "CREATE INDEX IF NOT EXISTS idx_beacon_events_device ON beacon_device_events(device_id, id)",
    # Numéros de balise retirés (balise supprimée) : `devices.next_uid` ne les
    # réattribue jamais, car AubeLink peut encore associer ce numéro à un drone
    # si la dissociation n'a pas pu lui parvenir.
    """CREATE TABLE IF NOT EXISTS beacon_retired_uids (
    device_uid TEXT PRIMARY KEY,   -- AUBE-BCN-000001
    retired_at TEXT NOT NULL       -- suppression (ISO 8601 UTC)
)""",
]

TABLE_NAMES = ("beacon_devices", "beacon_flights", "beacon_telemetry", "beacon_device_events",
               "beacon_retired_uids")
