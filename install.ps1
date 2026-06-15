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
} else {
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
    } else {
        Write-Error "Failed to install dependencies in .venv."
        Exit 1
    }
} else {
    Write-Error "requirements.txt was not found in the current directory."
    Exit 1
}

# 5. Create required directories
$requiredDirs = @("logs", "context", "scripts", "prompts", "engine_instructions", "gem_trading_rules", "static", "cache")
foreach ($dir in $requiredDirs) {
    $dirPath = Join-Path $PSScriptRoot $dir
    if (-not (Test-Path $dirPath)) {
        Write-Host "Creating directory: $dir..." -ForegroundColor Yellow
        New-Item -ItemType Directory -Force -Path $dirPath | Out-Null
        Write-Host "Directory '$dir' created successfully." -ForegroundColor Green
    }
}

# 6. Offer to install dependencies globally
$choice = Read-Host "Would you also like to install dependencies globally? (y/N)"
if ($choice -eq 'y' -or $choice -eq 'Y') {
    Write-Host "Installing dependencies globally..." -ForegroundColor Yellow
    & python -m pip install -r requirements.txt
    if ($?) {
        Write-Host "Dependencies successfully installed globally!" -ForegroundColor Green
    } else {
        Write-Warning "Global dependency installation failed or partially succeeded."
    }
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Setup Completed! You can run the application using:" -ForegroundColor Green
Write-Host "  .venv\Scripts\python.exe python\web_server.py" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Green
