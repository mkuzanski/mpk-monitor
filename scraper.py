"""
scraper.py
----------
Pobiera stronę https://mpk.lodz.pl/rozklady/utrudnienia.jsp i parsuje
tabelę utrudnień do listy słowników.

Struktura strony (stan na 2026-07):
    Tabela z nagłówkiem: Nr linii | Utrudnienie w ruchu | Zmiana sytuacji | (link lokalizacji)
    Każdy wpis zajmuje DWA wiersze <tr>:
        wiersz 1: numery linii, opis utrudnienia, opis zmiany sytuacji, link "Pokaż lokalizację"
        wiersz 2: (puste) "Dodano dnia DATA o godzinie GODZINA" (x2, dla obu kolumn)

Strona jest serwowana w kodowaniu ISO-8859-2 (Windows-1250/Latin-2 dla polskiego),
co trzeba jawnie ustawić - inaczej polskie znaki będą "krzakami".
"""

import re
import logging
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://mpk.lodz.pl/rozklady/utrudnienia.jsp"
ENCODING = "iso-8859-2"
USER_AGENT = "Mozilla/5.0 (compatible; MPKUtrudnieniaMonitor/1.0; +local-script)"
TIMEOUT_SECONDS = 20

DODANO_RE = re.compile(r"Dodano\s+dnia\s+(.+?)\s+o\s+godzinie\s+(.+)", re.IGNORECASE)

log = logging.getLogger(__name__)


class FetchError(Exception):
    pass


def fetch_html(url: str = URL) -> str:
    """Pobiera surowy HTML strony, z poprawnym kodowaniem."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise FetchError(f"Błąd pobierania strony: {e}") from e

    resp.encoding = ENCODING
    return resp.text


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _find_target_table(soup: BeautifulSoup):
    """Znajduje tabelę zawierającą nagłówek 'Utrudnienie w ruchu'."""
    for table in soup.find_all("table"):
        header_text = table.get_text(" ", strip=True)
        if "Utrudnienie w ruchu" in header_text and "Zmiana sytuacji" in header_text:
            return table
    return None


def parse_utrudnienia(html: str, base_url: str = URL) -> list[dict]:
    """
    Parsuje HTML strony utrudnień i zwraca listę słowników:
        {
            "linie": str,
            "utrudnienie": str,
            "zmiana_sytuacji": str,
            "data_dodania_raw": str | None,
            "data_zmiany_raw": str | None,
            "lokalizacja_url": str | None,
        }

    Jeśli strona aktualnie nie ma żadnych utrudnień, zwraca pustą listę.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = _find_target_table(soup)
    if table is None:
        log.warning("Nie znaleziono tabeli utrudnień na stronie - prawdopodobnie brak utrudnień "
                    "albo zmieniła się struktura strony.")
        return []

    rows = table.find_all("tr")
    # Pomijamy wiersz(e) nagłówkowe - identyfikujemy je po zawartości "Nr linii"
    data_rows = [r for r in rows if "Nr linii" not in r.get_text()]

    entries = []
    i = 0
    while i < len(data_rows):
        row1 = data_rows[i]
        cells1 = row1.find_all("td")

        # Wiersz "Zamknij" / stopka na końcu tabeli - kończymy
        if len(cells1) < 3:
            i += 1
            continue

        linie = _clean(cells1[0].get_text(" ", strip=True))
        utrudnienie = _clean(cells1[1].get_text(" ", strip=True))
        zmiana_sytuacji = _clean(cells1[2].get_text(" ", strip=True))

        lokalizacja_url = None
        if len(cells1) > 3:
            link = cells1[3].find("a")
            if link and link.get("href"):
                lokalizacja_url = urljoin(base_url, link["href"])

        # Pomijamy puste/śmieciowe wiersze (np. resztki stopki tabeli)
        if not utrudnienie and not zmiana_sytuacji:
            i += 1
            continue

        data_dodania_raw = None
        data_zmiany_raw = None

        # Kolejny wiersz zwykle zawiera daty "Dodano dnia ..."
        if i + 1 < len(data_rows):
            row2 = data_rows[i + 1]
            cells2 = row2.find_all("td")
            row2_text_joined = " ".join(_clean(c.get_text(" ", strip=True)) for c in cells2)
            if "Dodano dnia" in row2_text_joined:
                if len(cells2) > 1:
                    data_dodania_raw = _clean(cells2[1].get_text(" ", strip=True)) or None
                if len(cells2) > 2:
                    data_zmiany_raw = _clean(cells2[2].get_text(" ", strip=True)) or None
                i += 2  # zużyliśmy oba wiersze
            else:
                i += 1  # tylko jeden wiersz danych, bez wiersza z datami
        else:
            i += 1

        entries.append({
            "linie": linie,
            "utrudnienie": utrudnienie,
            "zmiana_sytuacji": zmiana_sytuacji,
            "data_dodania_raw": data_dodania_raw,
            "data_zmiany_raw": data_zmiany_raw,
            "lokalizacja_url": lokalizacja_url,
        })

    return entries


def fetch_and_parse(url: str = URL) -> list[dict]:
    html = fetch_html(url)
    return parse_utrudnienia(html, base_url=url)
