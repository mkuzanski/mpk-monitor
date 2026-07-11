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

## Dlaczego harmonogram odpala się rzadko (i jak to naprawić)

Jeśli widzisz, że workflow uruchamia się kilka razy dziennie zamiast co
10 minut, w losowych odstępach — to **nie błąd konfiguracji**, tylko
udokumentowane zachowanie GitHub Actions. `on: schedule` trafia do tej samej
globalnej kolejki co wszystkie inne zadania Actions na całym GitHubie, bez
żadnej gwarancji SLA. Przy częstych harmonogramach (rząd 5-10 minut) duża
część zaplanowanych odpaleń jest **cicho pomijana** — nie opóźniona, tylko
w ogóle nieuruchomiona. Im częstszy harmonogram i im większe obciążenie
platformy, tym więcej odpaleń znika. To nasiliło się szczególnie w 2026 r.
i dotyczy zwłaszcza kont darmowych oraz częstych cronów.

**Rozwiązanie: przenieś harmonogram poza kolejkę GitHub Actions.**

Zamiast polegać na wewnętrznym `schedule` GitHuba, użyj zewnętrznego,
darmowego serwisu cron (np. [cron-job.org](https://cron-job.org)), który co
10 minut woła GitHub API i każe *natychmiast* odpalić workflow przez
`workflow_dispatch`. GitHub wtedy tylko wykonuje zadanie — nie musi go
samodzielnie planować, więc znika problem gubienia uruchomień.

### Krok 1: Utwórz token dostępu do GitHub API

GitHub → **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**

- Repository access: wybierz tylko swoje repo (np. `mpk-monitor`)
- Permissions: **Actions → Read and write**
- Skopiuj wygenerowany token (zaczyna się od `github_pat_...`) - będzie
  potrzebny w kroku 3. Traktuj go jak hasło.

### Krok 2: Znajdź nazwę pliku workflow

To po prostu nazwa pliku: `scrape.yml` (w `.github/workflows/scrape.yml`).

### Krok 3: Skonfiguruj cron-job.org

1. Załóż darmowe konto na [cron-job.org](https://cron-job.org)
2. **Create cronjob**:
   - **URL**: `https://api.github.com/repos/TWOJ-LOGIN/TWOJE-REPO/actions/workflows/scrape.yml/dispatches`
   - **Request method**: `POST`
   - **Request body** (JSON): `{"ref":"main"}` (albo nazwa Twojej głównej gałęzi)
   - **Headers**:
     - `Authorization: Bearer TWÓJ_TOKEN_Z_KROKU_1`
     - `Accept: application/vnd.github+json`
     - `Content-Type: application/json`
   - **Schedule**: co 10 minut, w godz. 6:00-22:00 — cron-job.org pozwala
     ustawić to bezpośrednio w **czasie polskim** (wybierz strefę czasową
     `Europe/Warsaw` w ustawieniach zadania), więc unikasz przeliczania na
     UTC i problemów z czasem letnim/zimowym, które są konieczne przy
     wewnętrznym `schedule` GitHuba.
3. Zapisz i przetestuj przyciskiem "Run now" - powinieneś zobaczyć nowy
   przebieg w zakładce **Actions** swojego repo w ciągu kilku sekund.

Odpowiedź `204 No Content` z GitHub API oznacza sukces (dispatch nie zwraca
treści). Błąd `401`/`403` zwykle oznacza zły/wygasły token albo za wąskie
uprawnienia; `404` zwykle oznacza literówkę w nazwie repo/pliku workflow.

### Co zostaje w repo

Harmonogram `schedule` w `scrape.yml` **zostaje jako rzadki fallback** (co
ok. 2h) na wypadek, gdyby zewnętrzny cron przestał działać — sam w sobie
nie zapewni monitoringu co 10 minut, ale lepiej mieć jakąkolwiek siatkę
bezpieczeństwa niż żadną.

## Darmowa strona z listą utrudnień (paginacja + filtr dat)

W katalogu `docs/` jest gotowa, samodzielna strona (`index.html`, bez
frameworków ani kroku budowania), która pokazuje listę utrudnień: 25 na
stronę, od najnowszych, z filtrem zakresu dat. Łączy się **bezpośrednio z
Turso przez surowe HTTP API** — nie potrzeba żadnego backendu ani
serwera pośredniczącego.

```
Przeglądarka użytkownika
        │
        ▼
   docs/index.html  ──►  Turso HTTP API (/v2/pipeline)  ──►  baza utrudnienia
```

### Krok 1: Utwórz token TYLKO do odczytu

To ważne — nie używaj do tego tokenu z kroku 1 głównej instrukcji (tamten
ma prawa zapisu i trafia tylko do sekretów GitHub Actions, nigdy do
publicznego frontendu):

```bash
turso db tokens create mpk-utrudnienia --read-only
turso db show mpk-utrudnienia --http-url
```

### Krok 2: Uzupełnij konfigurację w `docs/index.html`

Na początku sekcji `<script>` w pliku jest blok `CONFIG`:

```js
const CONFIG = {
  TURSO_HTTP_URL: "WKLEJ_TUTAJ_HTTP_URL",      // wynik --http-url z kroku 1
  TURSO_READ_ONLY_TOKEN: "WKLEJ_TUTAJ_TOKEN",  // token z flagą --read-only
};
```

Wklej tam swoje wartości i zapisz plik.

### Krok 3: Włącz GitHub Pages

W repo: **Settings → Pages → Build and deployment → Source: Deploy from a
branch → Branch: `main`, folder: `/docs`** → Save.

Po chwili strona będzie dostępna pod adresem
`https://TWOJ-LOGIN.github.io/TWOJE-REPO/`.

### Ważna uwaga o bezpieczeństwie tego rozwiązania

Token wklejony w `CONFIG` jest **widoczny publicznie** dla każdego, kto
zajrzy w źródło strony — to nieuniknione przy architekturze "czysty
frontend bez backendu". Dlatego:

- token musi być utworzony z flagą **`--read-only`** — wtedy nawet ktoś,
  kto go wyciągnie ze strony, może co najwyżej odczytać dane (a to i tak
  publiczne informacje o utrudnieniach), nie może niczego zmienić ani
  usunąć,
- najgorszy realny scenariusz to ktoś odpytujący Twoją bazę cudzym kosztem
  darmowego limitu odczytów Turso (500 mln/mies. — praktycznie
  niewyczerpalne dla tej skali, patrz tabela niżej).

Jeśli mimo to zależy Ci na ukryciu tokenu (np. plan na przyszłość z danymi
wrażliwymi), rozwiązaniem jest dodanie małego proxy (np. darmowy Cloudflare
Worker), który trzyma token po swojej stronie i tylko przekazuje zapytania
SELECT. Nie ma tego w tej wersji, bo dla publicznych danych o utrudnieniach
komunikacyjnych to zbędna komplikacja — ale daj znać, jeśli chcesz, żebym
taki wariant przygotował.

## Test lokalny przed wrzuceniem na GitHub

Możesz przetestować cały przepływ lokalnie, zanim skonfigurujesz Turso —
`db.py` obsługuje też zwykły plik SQLite:

```bash
pip install -r requirements.txt

# Test z lokalnym plikiem SQLite (bez konta Turso)
TURSO_DATABASE_URL="test.db" python3 run.py

# Test z prawdziwą bazą Turso
export TURSO_DATABASE_URL="libsql://mpk-utrudnienia-twojlogin.turso.io"
export TURSO_AUTH_TOKEN="ey...twój-token..."
python3 run.py
```

## Ważne: biblioteka `libsql`, nie `libsql-client`

We wcześniejszej wersji tego kodu używałem pakietu `libsql-client`. **Turso
go zdeprecjonowało** — po migracji darmowego tieru z Fly.io na AWS (2025/2026)
jego połączenia oparte o WebSocket zaczęły zawodzić błędem w stylu:

```
aiohttp.client_exceptions.WSServerHandshakeError: 400/500,
message='Invalid response status', url='wss://...turso.io'
```

Ten kod używa już nowego, oficjalnego pakietu **`libsql`** (`pip install libsql`),
który łączy się przez HTTP zamiast WebSocket i ma interfejs praktycznie
identyczny z wbudowanym modułem `sqlite3` (`connect`, `execute`, `commit`,
`fetchone`/`fetchall` zwracające zwykłe krotki). Jeśli w swoim projekcie masz
gdzieś jeszcze `libsql-client` albo `libsql-experimental` w `requirements.txt` —
zamień je na `libsql`.

## Uwaga o strefie czasowej fallbacku w scrape.yml

Ten wewnętrzny `schedule` GitHuba to teraz tylko rzadka siatka
bezpieczeństwa (patrz sekcja wyżej), więc jego dokładna godzina nie jest
krytyczna. Warto jednak wiedzieć: GitHub Actions liczy cron **w UTC**, a
strona MPK aktualizowana jest w godz. 6:00-22:00 **czasu polskiego** (UTC+2
latem / UTC+1 zimą). Fallback `"17 */2 * * *"` (co ok. 2h, o niepełnej
godzinie) i tak złapie sensowną część dnia niezależnie od pory roku.

Dla głównego harmonogramu w cron-job.org możesz spokojnie wybrać strefę
`Europe/Warsaw` wprost w ich interfejsie i zapomnieć o przeliczaniu na UTC.

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
  ten sam sqlite3-podobny interfejs (`connect`, `execute`, `commit`,
  `fetchone`/`fetchall`), jaki dokumentacja Turso deklaruje dla pakietu
  `libsql` — to samo zachowanie co przy prawdziwej bazie Turso,
- **nie przetestowałem** faktycznego połączenia sieciowego z Turso ani
  realnego działania paczki `libsql` (kod jest zgodny z oficjalną,
  aktualną dokumentacją Turso z lipca 2026, ale warto zweryfikować
  pierwszym uruchomieniem lokalnym z prawdziwymi danymi logowania —
  patrz "Test lokalny" wyżej). Poprzednia wersja tego kodu używała
  zdeprecjonowanego pakietu `libsql-client` i faktycznie nie działała
  na produkcyjnych bazach Turso hostowanych na AWS — dzięki zgłoszonemu
  przez Ciebie błędowi to zostało poprawione.

## Jeśli strona MPK zwróci błąd 403

Może się zdarzyć, że serwer MPK blokuje żądania bez nagłówków
przeglądarkowych. Jeśli tak się stanie w GitHub Actions, dopisz w
`scraper.py` dodatkowe nagłówki (`Accept`, `Accept-Language`, `Referer`) —
patrz komentarz w kodzie `USER_AGENT`.
