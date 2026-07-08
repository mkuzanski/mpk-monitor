#!/usr/bin/env python3
"""
run.py
------
Pojedyncze uruchomienie monitora utrudnień MPK Łódź - pobiera stronę
i zapisuje dane do bazy Turso. Przeznaczone do uruchamiania przez
GitHub Actions (cron) albo ręcznie z terminala.

Wymagane zmienne środowiskowe:
    TURSO_DATABASE_URL   np. libsql://twoja-baza-twojorg.turso.io
    TURSO_AUTH_TOKEN     token wygenerowany przez `turso db tokens create <nazwa-bazy>`

Test lokalny bez konta Turso (baza jako zwykły plik SQLite obok skryptu):
    TURSO_DATABASE_URL=utrudnienia_test.db python3 run.py
"""

import os
import sys
import logging

from scraper import fetch_and_parse, FetchError, URL
from db import get_connection, init_db, sync_entries, log_fetch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mpk_monitor")


def main():
    db_url = os.environ.get("TURSO_DATABASE_URL")
    auth_token = os.environ.get("TURSO_AUTH_TOKEN")  # niewymagany dla lokalnego pliku

    if not db_url:
        log.error(
            "Brak zmiennej środowiskowej TURSO_DATABASE_URL. "
            "Ustaw ją (sekret GitHub Actions albo zmienna lokalna)."
        )
        sys.exit(1)

    conn = get_connection(db_url, auth_token)
    init_db(conn)

    try:
        entries = fetch_and_parse(URL)
    except FetchError as e:
        log.error("Nie udało się pobrać strony: %s", e)
        log_fetch(conn, success=False, error_message=str(e))
        conn.close()
        sys.exit(1)
    except Exception as e:  # nieoczekiwany błąd parsowania - nie chcemy niejasnego tracebacku w Actions
        log.exception("Nieoczekiwany błąd podczas pobierania/parsowania strony")
        log_fetch(conn, success=False, error_message=str(e))
        conn.close()
        sys.exit(1)

    summary = sync_entries(conn, entries)
    log_fetch(conn, success=True, entries_found=len(entries))

    log.info(
        "Sprawdzono stronę: %d aktywnych utrudnień (nowe: %d, wznowione: %d, "
        "zamknięte: %d, bez zmian: %d)",
        len(entries), summary["new"], summary["updated"], summary["closed"], summary["unchanged"],
    )

    conn.close()


if __name__ == "__main__":
    main()
