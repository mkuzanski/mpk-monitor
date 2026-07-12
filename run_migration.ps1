<#
.SYNOPSIS
    Uruchamia migrate_dedupe.py (scalanie duplikatów w bazie Turso) z PowerShell.

.DESCRIPTION
    Odpowiednik instrukcji bash (export + python3 migrate_dedupe.py), dostosowany
    do Windows/PowerShell. Dodatkowo dla bezpieczeństwa:
      - zmienne środowiskowe TURSO_DATABASE_URL / TURSO_AUTH_TOKEN ustawia TYLKO
        na czas działania tego okna PowerShell (czyści je na końcu, nie zostają
        globalnie w systemie),
      - token wpisujesz w trybie ukrytym (jak hasło),
      - ZAWSZE najpierw robi dry-run i pokazuje wynik, dopiero po Twoim
        potwierdzeniu odpala prawdziwą migrację.

.PARAMETER RepoPath
    Ścieżka do folderu z plikami migrate_dedupe.py i db.py. Domyślnie bieżący katalog.

.PARAMETER DatabaseUrl
    Adres bazy Turso, np. libsql://mpk-utrudnienia-twojlogin.turso.io
    Jeśli pominiesz, skrypt zapyta.

.PARAMETER AuthToken
    Token dostępu do Turso. Jeśli pominiesz, skrypt zapyta (wpisywanie ukryte).

.EXAMPLE
    .\run_migration.ps1

.EXAMPLE
    .\run_migration.ps1 -RepoPath "C:\projekty\mpk-monitor"
#>

param(
    [string]$RepoPath = (Get-Location).Path,
    [string]$DatabaseUrl,
    [string]$AuthToken
)

$ErrorActionPreference = "Stop"

# --- 1. Sprawdzenie, czy Python jest dostępny w PATH ---
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $pythonCmd) {
    Write-Host "Nie znaleziono polecenia 'python' ani 'python3' w PATH." -ForegroundColor Red
    Write-Host "Zainstaluj Pythona (python.org) i upewnij się, że jest dodany do PATH." -ForegroundColor Red
    exit 1
}
$python = $pythonCmd.Source

# --- 2. Sprawdzenie, czy potrzebne pliki istnieją ---
$scriptPath = Join-Path $RepoPath "migrate_dedupe.py"
$dbPath = Join-Path $RepoPath "db.py"

if (-not (Test-Path $scriptPath)) {
    Write-Host "Nie znaleziono: $scriptPath" -ForegroundColor Red
    Write-Host "Podaj -RepoPath wskazujący na folder z projektem, np.:" -ForegroundColor Yellow
    Write-Host "    .\run_migration.ps1 -RepoPath `"C:\projekty\mpk-monitor`"" -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $dbPath)) {
    Write-Host "Nie znaleziono: $dbPath" -ForegroundColor Red
    Write-Host "migrate_dedupe.py importuje db.py, więc musi leżeć w tym samym folderze." -ForegroundColor Red
    exit 1
}

# --- 3. Zebranie danych logowania do Turso ---
if (-not $DatabaseUrl) {
    $DatabaseUrl = Read-Host "Podaj TURSO_DATABASE_URL (np. libsql://mpk-utrudnienia-twojlogin.turso.io)"
}
if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    Write-Host "TURSO_DATABASE_URL nie może być puste." -ForegroundColor Red
    exit 1
}

if (-not $AuthToken) {
    $secureToken = Read-Host "Podaj TURSO_AUTH_TOKEN (wpisywanie ukryte)" -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $AuthToken = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if ([string]::IsNullOrWhiteSpace($AuthToken)) {
    Write-Host "TURSO_AUTH_TOKEN nie może być puste." -ForegroundColor Red
    exit 1
}

# Ustawiane TYLKO w tym procesie PowerShell - znikają po zamknięciu okna
# albo po wyczyszczeniu w bloku finally na końcu tego skryptu.
$env:TURSO_DATABASE_URL = $DatabaseUrl
$env:TURSO_AUTH_TOKEN = $AuthToken

Push-Location $RepoPath
try {
    # --- 4. Zawsze najpierw dry-run ---
    Write-Host ""
    Write-Host "=== KROK 1/2: DRY-RUN (nic nie zostanie zapisane do bazy) ===" -ForegroundColor Cyan
    Write-Host ""
    & $python migrate_dedupe.py --dry-run
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Dry-run zakończył się błędem (kod $LASTEXITCODE). Przerywam - sprawdź komunikaty powyżej." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    # --- 5. Potwierdzenie od użytkownika ---
    Write-Host ""
    $confirmation = Read-Host "Wynik dry-run wygląda dobrze? Wprowadzić te zmiany NAPRAWDĘ do bazy? (tak/nie)"
    if ($confirmation -notin @("tak", "t", "yes", "y")) {
        Write-Host "Przerwano - baza NIE została zmieniona." -ForegroundColor Yellow
        exit 0
    }

    # --- 6. Prawdziwe uruchomienie ---
    Write-Host ""
    Write-Host "=== KROK 2/2: PRAWDZIWA MIGRACJA ===" -ForegroundColor Cyan
    Write-Host ""
    & $python migrate_dedupe.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Migracja zakończyła się błędem (kod $LASTEXITCODE)." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Gotowe." -ForegroundColor Green
}
finally {
    Pop-Location
    # Sprzątanie sekretów ze zmiennych środowiskowych tego procesu
    Remove-Item Env:\TURSO_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:\TURSO_AUTH_TOKEN -ErrorAction SilentlyContinue
}
