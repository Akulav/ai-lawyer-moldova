"""SQLite schema and connection helper. WAL lets the crawler write while the UI reads."""
import sqlite3

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues(
  id INTEGER PRIMARY KEY,
  title TEXT, published TEXT,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | listed | done | missing | no_text | error
  n_acts INTEGER DEFAULT 0, priority INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0, error TEXT
);
CREATE INDEX IF NOT EXISTS issues_status ON issues(status, priority DESC);

CREATE TABLE IF NOT EXISTS acts(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,          -- constitutie | lege | cod | hotarare | decret | ordin | regulament | dispozitie | decizie | tratat | alt
  number TEXT, num INTEGER, year INTEGER,
  title TEXT NOT NULL,
  adopted TEXT, published TEXT,
  mo_issue INTEGER, mo_art INTEGER, mo_ref TEXT,
  status TEXT NOT NULL DEFAULT 'presupus_in_vigoare',
      -- presupus_in_vigoare | abrogat | abrogare_viitoare
  status_note TEXT, status_date TEXT,
  replaced_by INTEGER,
  future_effective TEXT,       -- date this act itself enters into force, when later than today
  pinned INTEGER DEFAULT 0,
  has_text INTEGER DEFAULT 0,
  is_consolidated INTEGER DEFAULT 0,
  current_version INTEGER,
  url TEXT, legis_url TEXT
);
CREATE INDEX IF NOT EXISTS acts_ref ON acts(num, year);
CREATE INDEX IF NOT EXISTS acts_status ON acts(status);
CREATE INDEX IF NOT EXISTS acts_issue ON acts(mo_issue);
CREATE VIRTUAL TABLE IF NOT EXISTS acts_fts USING fts5(stems, tokenize='unicode61');

CREATE TABLE IF NOT EXISTS act_domain(act_id INTEGER, domain TEXT,
  source TEXT DEFAULT 'kw',    -- kw (title keywords, tree seed) | ai (tagged by the model, see ai_tags)
  PRIMARY KEY(act_id, domain)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS act_domain_d ON act_domain(domain);

-- One row per act the model has judged, including those it placed in no domain, so none is asked twice.
CREATE TABLE IF NOT EXISTS ai_tags(
  act_id INTEGER PRIMARY KEY,
  domains TEXT,                -- comma-separated domain ids, empty when none fits
  reason TEXT, model TEXT, tagged TEXT
);

CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY,
  act_id INTEGER NOT NULL,
  label TEXT, effective TEXT, source TEXT, url TEXT,
  consolidated INTEGER DEFAULT 0, added TEXT,
  UNIQUE(act_id, effective, source)
);

CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY,
  act_id INTEGER NOT NULL, version_id INTEGER NOT NULL,
  ord INTEGER, path TEXT, art TEXT, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_act ON chunks(act_id, version_id, ord);
CREATE INDEX IF NOT EXISTS chunks_version ON chunks(version_id, ord);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(stems, tokenize='unicode61');

-- Links between acts. The target is stored by reference because it may not be indexed yet.
CREATE TABLE IF NOT EXISTS relations(
  id INTEGER PRIMARY KEY,
  from_act INTEGER NOT NULL,
  to_cls TEXT, to_num INTEGER, to_year INTEGER,
  rel TEXT NOT NULL,           -- modifica | abroga
  whole INTEGER DEFAULT 0,
  effective TEXT, evidence TEXT,
  UNIQUE(from_act, to_cls, to_num, to_year, rel)
);
CREATE INDEX IF NOT EXISTS rel_to ON relations(to_num, to_year);

CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY, title TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, conversation_id INTEGER, role TEXT, content TEXT, meta TEXT
);
"""


def connect() -> sqlite3.Connection:
    # A streaming response's generator resumes on different worker threads, one at a time.
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init() -> None:
    con = connect()
    try:
        # Added after the first release; CREATE IF NOT EXISTS leaves older tables as they were.
        cols = [r["name"] for r in con.execute("PRAGMA table_info(act_domain)")]
        if cols and "source" not in cols:
            con.execute("ALTER TABLE act_domain ADD COLUMN source TEXT DEFAULT 'kw'")
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()  # `with con` only commits; it never closes


def get_setting(con, key: str, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key: str, value) -> None:
    con.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    con.commit()
