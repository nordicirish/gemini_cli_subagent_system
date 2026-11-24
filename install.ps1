# GEM Trading Agent Framework - Dependency Installer Script
# Installs verified python dependencies inside .venv or globally on Windows.

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "💎 GEM Trading Agent Framework Installer" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check Python installation
$pythonPath = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonPath) {
    Write-Error "Python 3.10+ was not found in your PATH. Please install Python and try again."
    Exit 1
}

$pythonVersion = & python --version
Write-Host "Found Python: $pythonVersion" -ForegroundColor Green

# 2. Check if .venv exists, otherwise create it
$venvPath = Join-Path $PSScriptRoot ".venv"
if (-not (Test-Path $venvPath)) {
    Write-Host "Creating python virtual environment (.venv)..." -ForegroundColor Yellow
    & python -m venv .venv
    if (-not $?) {
        Write-Error "Failed to create virtual environment."
        Exit 1
    }
    Write-Host "Virtual environment created successfully." -ForegroundColor Green
}
else {
    Write-Host "Existing virtual environment (.venv) detected." -ForegroundColor Green
}

# 3. Upgrade pip inside the virtual environment
Write-Host "Upgrading pip inside virtual environment..." -ForegroundColor Yellow
& .venv\Scripts\python.exe -m pip install --upgrade pip
if (-not $?) {
    Write-Warning "Failed to upgrade pip inside virtual environment."
}

# 4. Install dependencies from requirements.txt into the virtual environment
if (Test-Path "requirements.txt") {
    Write-Host "Installing dependencies from requirements.txt into .venv..." -ForegroundColor Yellow
    & .venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($?) {
        Write-Host "Dependencies successfully installed inside .venv!" -ForegroundColor Green
    }
    else {
        Write-Error "Failed to install dependencies in .venv."
        Exit 1
    }
}
else {
    Write-Error "requirements.txt was not found in the current directory."
    Exit 1
}

# 4b. Install test-only JavaScript dependencies (jsdom, DOMPurify, marked) for the chat sanitizer tests.
#     Optional: the dashboard itself loads its own pinned CDN copies and does not need node_modules.
$npmPath = Get-Command npm -ErrorAction SilentlyContinue
if ($npmPath -and (Test-Path (Join-Path $PSScriptRoot "package.json"))) {
    Write-Host "Installing test-only JavaScript dependencies (npm ci)..." -ForegroundColor Yellow
    & npm ci --ignore-scripts --no-audit --no-fund
    if ($?) {
        Write-Host "JavaScript test dependencies installed." -ForegroundColor Green
    }
    else {
        Write-Warning "npm ci failed; chat sanitizer jsdom tests will be skipped."
    }
}
else {
    Write-Warning "npm not found; skipping test-only JavaScript dependencies (chat sanitizer jsdom tests will be skipped)."
}

# 5. Create required directories
$requiredDirs = @("logs", "context", "context/charts", "scripts", "prompts", "engine_instructions", "gem_trading_rules", "static", "cache")
foreach ($dir in $requiredDirs) {
    $dirPath = Join-Path $PSScriptRoot $dir
    if (-not (Test-Path $dirPath)) {
        Write-Host "Creating directory: $dir..." -ForegroundColor Yellow
        New-Item -ItemType Directory -Force -Path $dirPath | Out-Null
        Write-Host "Directory '$dir' created successfully." -ForegroundColor Green
    }
}

# 6. Initialize SQLite Persistence Database (context/gem.db)
$contextDir = Join-Path $PSScriptRoot "context"
$gemDbPath = Join-Path $contextDir "gem.db"
if (Test-Path $gemDbPath) {
    Write-Host "Existing SQLite persistence database (context/gem.db) detected; skipping cutover initialization." -ForegroundColor Green
}
else {
    Write-Host "Initializing SQLite persistence database (context/gem.db) via perform_sqlite_cutover()..." -ForegroundColor Yellow
    $seedDefaults = @{
        "ssot.json" = '{"mutable_state":{"portfolio_snapshot":[],"watched_tickers":[],"unallocated_cash_eur":0.0,"unallocated_cash_usd":0.0}}'
        "trade_lessons.json" = '{"trade_lessons":[]}'
        "decision_log.json" = '{"decision_log":[]}'
    }
    foreach ($seedName in $seedDefaults.Keys) {
        $seedPath = Join-Path $contextDir $seedName
        if (-not (Test-Path $seedPath)) {
            [System.IO.File]::WriteAllText($seedPath, $seedDefaults[$seedName], (New-Object System.Text.UTF8Encoding($false)))
            Write-Host "Created default seed file: context/$seedName" -ForegroundColor Yellow
        }
    }

    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    $cutoverScript = "import sys; sys.path.insert(0, sys.argv[1]); from python.persistence.service import perform_sqlite_cutover; res = perform_sqlite_cutover(repo_root=sys.argv[1]); print('SQLite cutover status:', res.get('status'), 'revision:', res.get('db_revision'))"
    & $venvPython -c $cutoverScript "$PSScriptRoot"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "SQLite persistence cutover failed."
        Exit 1
    }
    Write-Host "SQLite persistence database initialized successfully." -ForegroundColor Green
}

# 7. Register Antigravity Sidecar UI configuration
$sidecarDir = Join-Path $HOME ".gemini\config\sidecars\gem-dashboard"
if (-not (Test-Path $sidecarDir)) {
    New-Item -ItemType Directory -Force -Path $sidecarDir | Out-Null
}
$sidecarConfig = @{
    command = (Join-Path $venvPath "Scripts\python.exe")
    args = @((Join-Path $PSScriptRoot "scripts\launch_sidecar.py"))
    env = @{ GEM_SIDECAR = "1" }
    restart_policy = "always"
    display_name = "GEM Trading Dashboard"
    description = "Autonomous multi-agent portfolio intelligence platform with live market telemetry and Council deliberation."
    has_web_ui = $true
    ui_config = @{
        display_name = "GEM Council"
        views = @(
            @{
                title = "GEM Council Dashboard"
                path = "/"
                entrypoint = "SIDECAR_UI_ENTRYPOINT_AUX_PANE"
            }
        )
    }
} | ConvertTo-Json -Depth 5
Set-Content -Path (Join-Path $sidecarDir "sidecar.json") -Value $sidecarConfig -Encoding UTF8
Write-Host "Antigravity Sidecar configuration registered at: $sidecarDir\sidecar.json" -ForegroundColor Green

# 8. Offer to install dependencies globally
$choice = Read-Host "Would you also like to install dependencies globally? (y/N)"
if ($choice -eq 'y' -or $choice -eq 'Y') {
    Write-Host "Installing dependencies globally..." -ForegroundColor Yellow
    & python -m pip install -r requirements.txt
    if ($?) {
        Write-Host "Dependencies successfully installed globally!" -ForegroundColor Green
    }
    else {
        Write-Warning "Global dependency installation failed or partially succeeded."
    }
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Setup Completed! You can run the application using:" -ForegroundColor Green
Write-Host "  .venv\Scripts\python.exe python\web_server.py" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Green
