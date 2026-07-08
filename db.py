"""
db.py (edycja Turso - pakiet `libsql`)
---------------------------------------
WAŻNE: Turso zdeprecjonowało starszy pakiet `libsql-client` (oparty o WebSocket).
Po migracji darmowego tieru Turso z Fly.io na AWS (2025/2026) połączenia przez
WebSocket zaczęły zawodzić błędem w stylu:
    aiohttp.client_exceptions.WSServerHandshakeError: 400/500,
    message='Invalid response status', url='wss://...turso.io'

Ten plik używa nowego, oficjalnego pakietu `libsql` (pip install libsql),
który łączy się przez HTTP i ma interfejs praktycznie identyczny z wbudowanym
modułem `sqlite3` (connect, execute, commit, fetchone/fetchall na krotkach).

Ten sam kod działa:
  - lokalnie z plikiem SQLite:  database="utrudnienia_test.db"           (testy, bez konta Turso)
  - zdalnie z bazą w Turso:     database="libsql://twoja-baza-org.turso.io", auth_token="..."
"""

import hashlib
from datetime import datetime, timezone

import libsql

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS utrudnienia (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash       TEXT NOT NULL UNIQUE,
        linie              TEXT,               -- numery linii, np. "83" albo "75AB, N3A"
        utrudnienie        TEXT,               -- opis utrudnienia
        zmiana_sytuacji    TEXT,               -- opis zmiany / objazdu
        data_dodania_raw   TEXT,               -- tekst "Dodano dnia ... o godzinie ..." (utrudnienie)
        data_zmiany_raw    TEXT,               -- tekst "Dodano dnia ... o godzinie ..." (zmiana sytuacji)
        lokalizacja_url    TEXT,               -- link "Pokaż lokalizację" jeśli dostępny
        first_seen_at      TEXT NOT NULL,      -- kiedy scraper zobaczył wpis pierwszy raz (UTC ISO)
        last_seen_at       TEXT NOT NULL,      -- ostatnie potwierdzenie obecności wpisu (UTC ISO)
        disappeared_at     TEXT,               -- kiedy wpis zniknął ze strony (UTC ISO), NULL jeśli nadal aktywny
        active             INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_utrudnienia_active ON utrudnienia(active)",
    "CREATE INDEX IF NOT EXISTS idx_utrudnienia_hash ON utrudnienia(content_hash)",
    """
    CREATE TABLE IF NOT EXISTS fetch_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        fetched_at    TEXT NOT NULL,
        success       INTEGER NOT NULL,
        entries_found INTEGER,
        error_message TEXT
    )
    """,
]


def compute_hash(linie: str, utrudnienie: str, zmiana_sytuacji: str) -> str:
    """Hash treści wpisu - używany do rozpoznania czy to ten sam wpis co poprzednio."""
    raw = f"{linie.strip()}|{utrudnienie.strip()}|{zmiana_sytuacji.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection(database_url: str, auth_token: str | None = None):
    """
    Tworzy połączenie (interfejs jak sqlite3.connect).

    database_url:
      - "sciezka/do/lokalnego.db"            -> lokalny plik (testy, bez sieci/konta Turso)
      - "libsql://twoja-baza-org.turso.io"    -> baza Turso w chmurze
    auth_token:
      - wymagany dla adresu Turso, pomijany dla pliku lokalnego
    """
    if auth_token:
        return libsql.connect(database=database_url, auth_token=auth_token)
    return libsql.connect(database=database_url)


def init_db(conn):
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.commit()


def log_fetch(conn, success: bool, entries_found: int = None, error_message: str = None):
    conn.execute(
        "INSERT INTO fetch_log (fetched_at, success, entries_found, error_message) VALUES (?, ?, ?, ?)",
        [now_iso(), int(success), entries_found, error_message],
    )
    conn.commit()


def sync_entries(conn, entries: list[dict]) -> dict:
    """
    Synchronizuje aktualnie znalezione wpisy `entries` ze stanem bazy.
      - nowy wpis (nowy hash) -> INSERT
      - wpis już znany i wciąż aktywny -> odświeżenie last_seen_at
      - wpis znany, ale wcześniej zamknięty (np. wrócił) -> reaktywacja
      - wpis, który był aktywny, ale zniknął z bieżącego zestawu -> zamknięcie (active=0)

    Zwraca podsumowanie: {"new": n, "updated": n, "closed": n, "unchanged": n}
    """
    ts = now_iso()
    seen_hashes = set()
    summary = {"new": 0, "updated": 0, "closed": 0, "unchanged": 0}

    for e in entries:
        h = compute_hash(e["linie"], e["utrudnienie"], e["zmiana_sytuacji"])
        seen_hashes.add(h)

        cur = conn.execute("SELECT id, active FROM utrudnienia WHERE content_hash = ?", [h])
        row = cur.fetchone()

        if row is None:
            conn.execute(
                """
                INSERT INTO utrudnienia
                    (content_hash, linie, utrudnienie, zmiana_sytuacji,
                     data_dodania_raw, data_zmiany_raw, lokalizacja_url,
                     first_seen_at, last_seen_at, disappeared_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)
                """,
                [
                    h, e["linie"], e["utrudnienie"], e["zmiana_sytuacji"],
                    e.get("data_dodania_raw"), e.get("data_zmiany_raw"), e.get("lokalizacja_url"),
                    ts, ts,
                ],
            )
            summary["new"] += 1
        else:
            row_id, active = row[0], row[1]
            if active == 0:
                conn.execute(
                    "UPDATE utrudnienia SET active = 1, disappeared_at = NULL, last_seen_at = ? WHERE id = ?",
                    [ts, row_id],
                )
                summary["updated"] += 1
            else:
                conn.execute(
                    "UPDATE utrudnienia SET last_seen_at = ? WHERE id = ?",
                    [ts, row_id],
                )
                summary["unchanged"] += 1

    cur_active = conn.execute("SELECT id, content_hash FROM utrudnienia WHERE active = 1")
    for row in cur_active.fetchall():
        row_id, content_hash = row[0], row[1]
        if content_hash not in seen_hashes:
            conn.execute(
                "UPDATE utrudnienia SET active = 0, disappeared_at = ? WHERE id = ?",
                [ts, row_id],
            )
            summary["closed"] += 1

    conn.commit()
    return summary
