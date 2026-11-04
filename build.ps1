# Builds dist\ChessCoach with a ready-to-run ChessCoach.exe
# Usage:  .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --quiet --upgrade pyinstaller
python -m pip install --quiet -r requirements.txt

python -m PyInstaller --noconfirm --clean --onedir --name ChessCoach `
    --add-data "chess_coach\mock_board.html;chess_coach" `
    --add-data "userscript;userscript" `
    run.py

$out = "dist\ChessCoach"
New-Item -ItemType Directory -Force "$out\engine" | Out-Null

if (Test-Path "engine\stockfish.exe") {
    Copy-Item "engine\stockfish.exe" "$out\engine\stockfish.exe" -Force
} else {
    Write-Warning "engine\stockfish.exe not found - download Stockfish into engine\ first"
}

Copy-Item "userscript" "$out\userscript" -Recurse -Force
Copy-Item "README.md" "$out\README.md" -Force

$instruction = "ИНСТРУКЦИЯ-ДЛЯ-ДРУГА.txt"
if (Test-Path $instruction) {
    $text = Get-Content -Raw -Encoding UTF8 $instruction
    [System.IO.File]::WriteAllText((Join-Path $PWD "$out\ИНСТРУКЦИЯ.txt"), $text, (New-Object System.Text.UTF8Encoding($true)))
}

$size = [math]::Round((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "Done: $out ($size MB)"
