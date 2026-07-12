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
    CREATE TABLE IF NOT EXISTS zmiana_historia (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        utrudnienie_id     INTEGER NOT NULL REFERENCES utrudnienia(id),
        zmiana_sytuacji    TEXT,               -- treść tej wersji opisu objazdu
        data_zmiany_raw    TEXT,               -- tekst "Dodano dnia..." zeskrobany razem z tą wersją
        recorded_at        TEXT NOT NULL       -- kiedy scraper po raz pierwszy zauważył tę wersję (UTC ISO)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_zmiana_historia_utrudnienie ON zmiana_historia(utrudnienie_id)",
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


def compute_hash(linie: str, utrudnienie: str) -> str:
    """
    Hash IDENTYFIKUJĄCY przeszkodę - używany do rozpoznania czy to ten sam wpis co poprzednio.

    Celowo NIE obejmuje `zmiana_sytuacji` (opis objazdu) - MPK potrafi
    aktualizować/dopisywać szczegóły objazdu dla tej samej, wciąż trwającej
    przeszkody. Gdyby ten hash zależał też od zmiana_sytuacji, każda taka
    aktualizacja tworzyłaby nowy wpis zamiast zaktualizować istniejący,
    co dawało w bazie duplikaty tej samej, realnej przeszkody.
    """
    raw = f"{linie.strip()}|{utrudnienie.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_zmiana_historia(conn, utrudnienie_id: int, zmiana_sytuacji: str, data_zmiany_raw, ts: str):
    """Zapisuje jedną zaobserwowaną wersję opisu objazdu do tabeli historii."""
    conn.execute(
        """
        INSERT INTO zmiana_historia (utrudnienie_id, zmiana_sytuacji, data_zmiany_raw, recorded_at)
        VALUES (?, ?, ?, ?)
        """,
        [utrudnienie_id, zmiana_sytuacji, data_zmiany_raw, ts],
    )


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
    Tożsamość wpisu = (linie, utrudnienie) - `zmiana_sytuacji` może się
    zmieniać w czasie bez tworzenia nowego wpisu (patrz compute_hash).

      - nowa przeszkoda (nowy hash) -> INSERT
      - znana i wciąż aktywna, opis objazdu bez zmian -> odświeżenie last_seen_at
      - znana i wciąż aktywna, ale zmienił się opis objazdu -> aktualizacja
        zmiana_sytuacji w tym samym wierszu (bez tworzenia duplikatu)
      - znana, ale wcześniej zamknięta (np. wróciła) -> reaktywacja + odświeżenie opisu objazdu
      - była aktywna, ale zniknęła z bieżącego zestawu -> zamknięcie (active=0)

    Zwraca podsumowanie: {"new": n, "updated": n, "closed": n, "unchanged": n}
    ("updated" obejmuje teraz zarówno reaktywacje, jak i zmiany opisu objazdu)
    """
    ts = now_iso()
    seen_hashes = set()
    summary = {"new": 0, "updated": 0, "closed": 0, "unchanged": 0}

    for e in entries:
        h = compute_hash(e["linie"], e["utrudnienie"])
        seen_hashes.add(h)

        cur = conn.execute(
            "SELECT id, active, zmiana_sytuacji FROM utrudnienia WHERE content_hash = ?", [h]
        )
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
            # Nie polegamy na lastrowid (niepewne wsparcie w kliencie HTTP) -
            # content_hash jest unikalny, więc bezpiecznie doczytujemy nowe id.
            new_id = conn.execute(
                "SELECT id FROM utrudnienia WHERE content_hash = ?", [h]
            ).fetchone()[0]
            _log_zmiana_historia(conn, new_id, e["zmiana_sytuacji"], e.get("data_zmiany_raw"), ts)
            summary["new"] += 1
        else:
            row_id, active, stored_zmiana = row[0], row[1], row[2]
            content_changed = (stored_zmiana or "") != (e["zmiana_sytuacji"] or "")

            if active == 0:
                # Ta sama przeszkoda wróciła po tym jak wcześniej zniknęła - reaktywacja,
                # przy okazji odświeżamy tekst objazdu na aktualny i zapisujemy tę
                # wersję do historii (reaktywacja to odrębne, warte odnotowania zdarzenie).
                conn.execute(
                    """
                    UPDATE utrudnienia
                    SET active = 1, disappeared_at = NULL, last_seen_at = ?,
                        zmiana_sytuacji = ?, data_zmiany_raw = ?
                    WHERE id = ?
                    """,
                    [ts, e["zmiana_sytuacji"], e.get("data_zmiany_raw"), row_id],
                )
                _log_zmiana_historia(conn, row_id, e["zmiana_sytuacji"], e.get("data_zmiany_raw"), ts)
                summary["updated"] += 1
            elif content_changed:
                # Przeszkoda wciąż ta sama, ale MPK zaktualizowało opis objazdu -
                # aktualizujemy treść w tym samym wierszu (bez duplikatu) i dopisujemy
                # nową wersję do historii, żeby nic nie zginęło.
                conn.execute(
                    """
                    UPDATE utrudnienia
                    SET last_seen_at = ?, zmiana_sytuacji = ?, data_zmiany_raw = ?
                    WHERE id = ?
                    """,
                    [ts, e["zmiana_sytuacji"], e.get("data_zmiany_raw"), row_id],
                )
                _log_zmiana_historia(conn, row_id, e["zmiana_sytuacji"], e.get("data_zmiany_raw"), ts)
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
