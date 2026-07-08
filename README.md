# Monitor utrudnień MPK Łódź — wersja darmowa (GitHub Actions + Turso)

Ten wariant nie wymaga żadnego serwera: **GitHub Actions** cyklicznie odpala
skrypt w chmurze GitHuba, a dane trafiają do **Turso** — darmowej bazy w
chmurze w 100% kompatybilnej z SQLite. Koszt: **0 zł**, o ile mieścisz się
w darmowych limitach (patrz niżej — dla tej skali są nieosiągalne w praktyce).

## Jak to działa

```
GitHub Actions (cron, co 10 min)
        │
        ▼
   run.py  ──►  scraper.py (pobiera i parsuje mpk.lodz.pl)
        │
        ▼
   db.py  ──►  Turso (baza w chmurze, protokół libSQL/HTTP)
```

Za każdym razem GitHub odpala "czysty" kontener bez pamięci poprzednich
uruchomień — dlatego bazy NIE trzymamy jako pliku w repo, tylko w Turso,
które jest dostępne przez sieć i pamięta stan między uruchomieniami.

## Krok 1: Załóż konto i bazę w Turso

```bash
# Instalacja CLI (Linux/macOS/WSL)
curl -sSfL https://get.tur.so/install.sh | bash

# Logowanie (otworzy przeglądarkę, możesz zalogować się przez GitHub)
turso auth login

# Utworzenie bazy (nazwij ją np. mpk-utrudnienia)
turso db create mpk-utrudnienia

# Adres bazy - będzie potrzebny jako TURSO_DATABASE_URL
turso db show mpk-utrudnienia --url

# Token dostępowy - będzie potrzebny jako TURSO_AUTH_TOKEN
turso db tokens create mpk-utrudnienia
```

Zapisz sobie oba wyniki (URL zaczyna się od `libsql://...`, token to długi
ciąg znaków).

## Krok 2: Wrzuć ten kod do repozytorium na GitHubie

Struktura repo powinna wyglądać tak:

```
.github/workflows/scrape.yml
run.py
scraper.py
db.py
requirements.txt
```

```bash
git init
git add .
git commit -m "Monitor utrudnien MPK Lodz"
git remote add origin https://github.com/TWOJ-LOGIN/mpk-monitor.git
git push -u origin main
```

**Uwaga:** jeśli repo ma być prywatne, darmowe minuty GitHub Actions są
limitowane (2000 min/mies. na koncie darmowym). Dla publicznego repo minuty
są praktycznie nielimitowane. Przy interwale 10 min i zadaniu trwającym
~20-30 sekund, prywatne repo i tak spokojnie mieści się w limicie
(ok. 100-150 uruchomień dziennie × pół minuty ≈ kilkadziesiąt minut/mies.).

## Krok 3: Dodaj sekrety w ustawieniach repo

W GitHubie: **Settings → Secrets and variables → Actions → New repository secret**

Dodaj dwa sekrety:
- `TURSO_DATABASE_URL` — wynik `turso db show ... --url`
- `TURSO_AUTH_TOKEN` — wynik `turso db tokens create ...`

## Krok 4: Gotowe

Workflow (`.github/workflows/scrape.yml`) uruchomi się automatycznie zgodnie
z harmonogramem cron, a także można go odpalić ręcznie z zakładki **Actions**
w repo (przycisk "Run workflow" — przydatne do pierwszego testu).

## Test lokalny przed wrzuceniem na GitHub

Możesz przetestować cały przepływ lokalnie, zanim skonfigurujesz Turso —
`db.py` obsługuje też zwykły plik SQLite:

```bash
pip install -r requirements.txt

# Test z lokalnym plikiem SQLite (bez konta Turso)
TURSO_DATABASE_URL="file:test.db" python3 run.py

# Test z prawdziwą bazą Turso
export TURSO_DATABASE_URL="libsql://mpk-utrudnienia-twojlogin.turso.io"
export TURSO_AUTH_TOKEN="ey...twój-token..."
python3 run.py
```

## Uwaga o harmonogramie (cron) i strefie czasowej

GitHub Actions liczy cron **w UTC**, a strona MPK aktualizowana jest w
godz. 6:00-22:00 **czasu polskiego**. Polska to UTC+2 latem (CEST) i UTC+1
zimą (CET). Domyślny harmonogram w `scrape.yml`:

```yaml
- cron: "*/10 4-21 * * *"
```

to celowo szerszy zakres (4:00-21:00 UTC), żeby z zapasem obejmować oba
warianty czasu, kosztem kilku dodatkowych, "pustych" uruchomień na
początku/końcu dnia. Możesz go zawęzić lub rozszerzyć wedle potrzeby.

**Ważne zastrzeżenie GitHuba:** harmonogram `schedule` jest realizowany
"z najlepszą możliwą starannością" — przy dużym obciążeniu GitHuba
uruchomienia mogą się opóźniać o kilka-kilkanaście minut. Dla monitoringu
utrudnień komunikacyjnych to nie problem, ale nie jest to narzędzie do zadań
wymagających precyzji co do minuty.

## Darmowe limity — czy na pewno się zmieszczę?

| Zasób | Limit darmowy Turso | Realne zużycie tego projektu |
|---|---|---|
| Storage | 5 GB | Ta baza to po latach działania pewnie pojedyncze MB |
| Odczyty (row reads) | 500 mln/mies. | Kilka-kilkanaście SELECT-ów co 10 min = tysiące/mies. |
| Zapisy (row writes) | 10 mln/mies. | Pojedyncze INSERT/UPDATE co 10 min = tysiące/mies. |

Innymi słowy: dla tego projektu nie masz szans wyjść poza darmowy plan,
nawet uruchamiając go non-stop przez lata.

## Przeglądanie danych

Najprościej przez CLI Turso:

```bash
turso db shell mpk-utrudnienia
```

a w powłoce SQL:

```sql
SELECT linie, utrudnienie, first_seen_at FROM utrudnienia WHERE active = 1;
```

Możesz też podłączyć się do bazy z dowolnej aplikacji (np. dashboardu w
Pythonie/JS) używając tego samego `TURSO_DATABASE_URL` i `TURSO_AUTH_TOKEN` —
baza jest dostępna z internetu, nie tylko z GitHub Actions.

## Struktura bazy danych

Identyczna jak w wariancie "SQLite na VPS":

Tabela `utrudnienia`:

| Kolumna | Opis |
|---|---|
| `content_hash` | hash treści wpisu (linie+opis+zmiana) — wykrywa duplikaty |
| `linie` | numery linii, których dotyczy utrudnienie |
| `utrudnienie` | opis utrudnienia |
| `zmiana_sytuacji` | opis objazdu / zmiany sytuacji |
| `data_dodania_raw` | oryginalny tekst "Dodano dnia..." dla utrudnienia |
| `data_zmiany_raw` | oryginalny tekst "Dodano dnia..." dla zmiany sytuacji |
| `lokalizacja_url` | link "Pokaż lokalizację" jeśli podany |
| `first_seen_at` | kiedy scraper po raz pierwszy zauważył wpis (UTC) |
| `last_seen_at` | ostatnie potwierdzenie obecności wpisu (UTC) |
| `disappeared_at` | kiedy wpis zniknął (UTC), `NULL` jeśli nadal aktywny |
| `active` | `1` jeśli nadal widoczny na stronie, `0` jeśli zniknął |

Tabela `fetch_log` — log każdego uruchomienia (sukces/błąd, liczba wpisów).

## Czego nie przetestowano w tym środowisku

Środowisko, w którym przygotowałem ten kod, nie ma dostępu do PyPI ani do
prawdziwego konta Turso, więc:
- logikę zapytań SQL (`db.py`) przetestowałem lokalnym plikiem SQLite przez
  ten sam interfejs `file:`, jaki obsługuje `libsql-client` — to samo
  zachowanie co przy prawdziwej bazie Turso,
- **nie przetestowałem** faktycznego połączenia sieciowego z Turso ani
  realnego działania paczki `libsql-client` (kod jest zgodny z oficjalną
  dokumentacją Turso, ale warto zweryfikować pierwszym uruchomieniem
  lokalnym z prawdziwymi danymi logowania — patrz "Test lokalny" wyżej).

## Jeśli strona MPK zwróci błąd 403

Może się zdarzyć, że serwer MPK blokuje żądania bez nagłówków
przeglądarkowych. Jeśli tak się stanie w GitHub Actions, dopisz w
`scraper.py` dodatkowe nagłówki (`Accept`, `Accept-Language`, `Referer`) —
patrz komentarz w kodzie `USER_AGENT`.
