"""
db.py (edycja Turso / libSQL)
------------------------------
Warstwa dostępu do bazy Turso dla monitora utrudnień MPK Łódź.

Turso to baza w chmurze w 100% kompatybilna z SQLite (silnik libSQL).
Zamiast pliku .db na dysku, łączymy się z nią przez sieć - dzięki temu
GitHub Actions (który za każdym razem startuje "czysty" kontener bez
trwałego dysku) może bezpiecznie zapisywać dane między uruchomieniami.

Używa oficjalnego klienta `libsql-client` (pip install libsql-client).
Ten sam kod działa:
  - lokalnie z plikiem SQLite:  url="file:utrudnienia.db"   (do testów, bez konta Turso)
  - zdalnie z bazą w Turso:     url="libsql://twoja-baza-org.turso.io", auth_token="..."

Schemat tabel jest identyczny jak w wersji "czysty SQLite na VPS" -
patrz opis kolumn poniżej.
"""

import hashlib
from datetime import datetime, timezone

import libsql_client

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


def get_client(url: str, auth_token: str | None = None):
    """
    Tworzy synchronicznego klienta libSQL/Turso.

    url:
      - "file:sciezka/do/lokalnego.db"      -> lokalny plik (testy, bez sieci/konta Turso)
      - "libsql://twoja-baza-org.turso.io"   -> baza Turso w chmurze
    auth_token:
      - wymagany dla "libsql://", pomijany dla "file:"
    """
    kwargs = {}
    if auth_token:
        kwargs["auth_token"] = auth_token
    return libsql_client.create_client_sync(url, **kwargs)


def init_db(client):
    for stmt in SCHEMA_STATEMENTS:
        client.execute(stmt)


def log_fetch(client, success: bool, entries_found: int = None, error_message: str = None):
    client.execute(
        "INSERT INTO fetch_log (fetched_at, success, entries_found, error_message) VALUES (?, ?, ?, ?)",
        [now_iso(), int(success), entries_found, error_message],
    )


def sync_entries(client, entries: list[dict]) -> dict:
    """
    Synchronizuje aktualnie znalezione wpisy `entries` ze stanem bazy.
    Logika identyczna jak w wersji SQLite-na-dysku:
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

        rs = client.execute("SELECT id, active FROM utrudnienia WHERE content_hash = ?", [h])

        if len(rs.rows) == 0:
            client.execute(
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
            row = rs.rows[0]
            row_id = row["id"]
            active = row["active"]
            if active == 0:
                client.execute(
                    "UPDATE utrudnienia SET active = 1, disappeared_at = NULL, last_seen_at = ? WHERE id = ?",
                    [ts, row_id],
                )
                summary["updated"] += 1
            else:
                client.execute(
                    "UPDATE utrudnienia SET last_seen_at = ? WHERE id = ?",
                    [ts, row_id],
                )
                summary["unchanged"] += 1

    rs_active = client.execute("SELECT id, content_hash FROM utrudnienia WHERE active = 1")
    for row in rs_active.rows:
        if row["content_hash"] not in seen_hashes:
            client.execute(
                "UPDATE utrudnienia SET active = 0, disappeared_at = ? WHERE id = ?",
                [ts, row["id"]],
            )
            summary["closed"] += 1

    return summary
