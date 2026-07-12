#!/usr/bin/env python3
"""
migrate_dedupe.py
------------------
JEDNORAZOWY skrypt migracyjny (ale bezpieczny do wielokrotnego uruchomienia -
patrz niżej) dla baz utworzonych PRZED zmianą sposobu liczenia content_hash
(z 3 pól linie+utrudnienie+zmiana_sytuacji na 2 pola linie+utrudnienie).

PROBLEM, KTÓRY ROZWIĄZUJE:
Stare wiersze w tabeli `utrudnienia` mają content_hash policzony starym,
3-polowym wzorem. Nowy kod (db.py) liczy content_hash tylko z (linie,
utrudnienie). Bez tej migracji, przy najbliższym uruchomieniu scrapera
każdy istniejący wiersz "nie zostanie rozpoznany" (bo jego zapisany hash
nie zgadza się z tym, co teraz policzyłby compute_hash) i dostanie
DRUGI, świeży wiersz-duplikat zamiast zostać zaktualizowany.

CO ROBI:
  1. Grupuje wszystkie wiersze `utrudnienia` po (linie, utrudnienie).
  2. W grupach z więcej niż jednym wierszem (czyli duplikatach powstałych
     przed poprawką):
       - wybiera "kotwicę" (wiersz, który przetrwa) - preferuje aktywny,
         przy remisie/braku aktywnego: ten z najświeższym last_seen_at
       - dla KAŻDEGO wiersza w grupie bez własnych wpisów w zmiana_historia
         dopisuje jego zmiana_sytuacji jako wersję historyczną (żeby żadna
         wersja opisu objazdu nie przepadła)
       - przepina ewentualne istniejące zmiana_historia z "przegranych"
         wierszy na kotwicę (zamiast je tracić przy usuwaniu)
       - aktualizuje kotwicę: first_seen_at=MIN, last_seen_at=MAX,
         active=(czy ktokolwiek w grupie był aktywny), disappeared_at wg stanu,
         zmiana_sytuacji/data_zmiany_raw = wersja z najświeższym last_seen_at
       - usuwa "przegrane" wiersze
  3. We WSZYSTKICH wierszach (także tych bez duplikatów) przelicza
     content_hash na nowy schemat - to konieczne również dla wierszy,
     które nigdy nie miały duplikatu.

BEZPIECZNE DO WIELOKROTNEGO URUCHOMIENIA:
Drugie i kolejne uruchomienia nie znajdą już żadnych grup z więcej niż
jednym wierszem, a content_hash będzie już poprawny - skrypt nic wtedy
nie zmieni (poza ewentualnym uzupełnieniem brakującej historii, co też
jest bezpieczne, bo sprawdza czy historia już istnieje przed dopisaniem).

UŻYCIE:
    # najpierw ZAWSZE dry-run - pokazuje co by się zmieniło, nic nie zapisuje
    export TURSO_DATABASE_URL="libsql://twoja-baza-org.turso.io"
    export TURSO_AUTH_TOKEN="ey...twoj-token..."
    python3 migrate_dedupe.py --dry-run

    # gdy wynik dry-run wygląda sensownie, uruchom naprawdę:
    python3 migrate_dedupe.py

Test lokalny bez konta Turso:
    TURSO_DATABASE_URL="test.db" python3 migrate_dedupe.py --dry-run
"""

import os
import sys
import argparse
from collections import defaultdict

from db import get_connection, compute_hash, init_db


def fetch_all_rows(conn):
    cur = conn.execute("""
        SELECT id, content_hash, linie, utrudnienie, zmiana_sytuacji,
               data_dodania_raw, data_zmiany_raw, lokalizacja_url,
               first_seen_at, last_seen_at, disappeared_at, active
        FROM utrudnienia
    """)
    cols = ["id", "content_hash", "linie", "utrudnienie", "zmiana_sytuacji",
            "data_dodania_raw", "data_zmiany_raw", "lokalizacja_url",
            "first_seen_at", "last_seen_at", "disappeared_at", "active"]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def group_key(row):
    return ((row["linie"] or "").strip(), (row["utrudnienie"] or "").strip())


def history_count(conn, utrudnienie_id):
    cur = conn.execute(
        "SELECT COUNT(*) FROM zmiana_historia WHERE utrudnienie_id = ?", [utrudnienie_id]
    )
    return cur.fetchone()[0]


def backfill_history_if_missing(conn, row, dry_run) -> bool:
    """Jeśli wiersz nie ma jeszcze żadnej wersji w zmiana_historia, dopisuje
    jego bieżący zmiana_sytuacji jako pierwszą (jedyną znaną) wersję."""
    if history_count(conn, row["id"]) > 0:
        return False
    if not dry_run:
        conn.execute(
            """
            INSERT INTO zmiana_historia (utrudnienie_id, zmiana_sytuacji, data_zmiany_raw, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            [row["id"], row["zmiana_sytuacji"], row["data_zmiany_raw"], row["first_seen_at"]],
        )
    return True


def pick_anchor(group):
    """Wiersz, który przetrwa scalenie: preferuje aktywny, potem najświeższy."""
    active_rows = [r for r in group if r["active"] == 1]
    pool = active_rows if active_rows else group
    return max(pool, key=lambda r: r["last_seen_at"])


def most_recent(group):
    return max(group, key=lambda r: r["last_seen_at"])


def merge_group(conn, key, group, dry_run) -> None:
    linie, utrudnienie = key
    new_hash = compute_hash(linie, utrudnienie)

    backfilled = sum(1 for row in group if backfill_history_if_missing(conn, row, dry_run))

    anchor = pick_anchor(group)
    losers = [r for r in group if r["id"] != anchor["id"]]

    new_first_seen = min(r["first_seen_at"] for r in group)
    new_last_seen = max(r["last_seen_at"] for r in group)
    new_active = 1 if any(r["active"] == 1 for r in group) else 0
    if new_active:
        new_disappeared_at = None
    else:
        closed_dates = [r["disappeared_at"] for r in group if r["disappeared_at"]]
        new_disappeared_at = max(closed_dates) if closed_dates else new_last_seen

    latest = most_recent(group)

    print(f"  SCALANIE {key!r}: {len(group)} wiersze -> kotwica id={anchor['id']} "
          f"(usuwane id={[r['id'] for r in losers]}, dopisanych wersji historii={backfilled})")

    if dry_run:
        return

    for loser in losers:
        conn.execute(
            "UPDATE zmiana_historia SET utrudnienie_id = ? WHERE utrudnienie_id = ?",
            [anchor["id"], loser["id"]],
        )

    conn.execute(
        """
        UPDATE utrudnienia
        SET content_hash = ?, first_seen_at = ?, last_seen_at = ?,
            active = ?, disappeared_at = ?, zmiana_sytuacji = ?, data_zmiany_raw = ?
        WHERE id = ?
        """,
        [
            new_hash, new_first_seen, new_last_seen, new_active, new_disappeared_at,
            latest["zmiana_sytuacji"], latest["data_zmiany_raw"], anchor["id"],
        ],
    )

    for loser in losers:
        conn.execute("DELETE FROM utrudnienia WHERE id = ?", [loser["id"]])


def fix_single_row(conn, row, dry_run) -> bool:
    """Wiersz bez duplikatów - i tak trzeba przeliczyć mu content_hash na nowy
    schemat, i dopisać historię jeśli jeszcze jej nie ma."""
    linie, utrudnienie = (row["linie"] or "").strip(), (row["utrudnienie"] or "").strip()
    new_hash = compute_hash(linie, utrudnienie)
    hash_changed = new_hash != row["content_hash"]
    backfilled = backfill_history_if_missing(conn, row, dry_run)

    if not hash_changed and not backfilled:
        return False

    opis = []
    if hash_changed:
        opis.append("przeliczony hash")
    if backfilled:
        opis.append("dopisana wersja historii")
    print(f"  id={row['id']} ({linie!r}, {utrudnienie[:60]!r}...): {', '.join(opis)}")

    if not dry_run and hash_changed:
        conn.execute("UPDATE utrudnienia SET content_hash = ? WHERE id = ?", [new_hash, row["id"]])

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Migracja: scalanie duplikatów utrudnień + przeliczenie content_hash na nowy schemat"
    )
    parser.add_argument("--dry-run", action="store_true",
                         help="Tylko pokaż co by się zmieniło, bez zapisu do bazy")
    args = parser.parse_args()

    db_url = os.environ.get("TURSO_DATABASE_URL")
    auth_token = os.environ.get("TURSO_AUTH_TOKEN")
    if not db_url:
        print("Brak TURSO_DATABASE_URL w zmiennych środowiskowych.", file=sys.stderr)
        sys.exit(1)

    conn = get_connection(db_url, auth_token)
    init_db(conn)  # upewnia się, że tabela zmiana_historia istnieje (CREATE IF NOT EXISTS)

    rows = fetch_all_rows(conn)
    print(f"Wczytano {len(rows)} wierszy z tabeli utrudnienia.")

    groups = defaultdict(list)
    for row in rows:
        groups[group_key(row)].append(row)

    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    single_groups = {k: v for k, v in groups.items() if len(v) == 1}

    total_dup_rows = sum(len(v) for v in duplicate_groups.values())
    print(f"Znaleziono {len(duplicate_groups)} grup z duplikatami ({total_dup_rows} wierszy razem, "
          f"docelowo zostanie z nich {len(duplicate_groups)}).")

    if args.dry_run:
        print("\n--- TRYB DRY-RUN: nic nie zostanie zapisane do bazy ---\n")

    for key, group in duplicate_groups.items():
        merge_group(conn, key, group, args.dry_run)

    print(f"\nSprawdzanie {len(single_groups)} wierszy bez duplikatów pod kątem hasha/historii...")
    changed_single = sum(
        1 for group in single_groups.values() if fix_single_row(conn, group[0], args.dry_run)
    )
    print(f"Zaktualizowano {changed_single} z nich.")

    if args.dry_run:
        print("\nDRY-RUN zakończony - nic nie zapisano. Uruchom bez --dry-run, żeby wprowadzić zmiany.")
    else:
        conn.commit()
        print("\nZapisano zmiany.")

    conn.close()


if __name__ == "__main__":
    main()
