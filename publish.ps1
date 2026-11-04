# Publishes the project to GitHub and attaches the built zip to a release.
# Run:  powershell -ExecutionPolicy Bypass -File .\publish.ps1
# Safe to re-run: existing files are updated (sha is fetched automatically).
$ErrorActionPreference = "Continue"

$gh    = "C:\Program Files\GitHub CLI\gh.exe"
$owner = "kuza29"
$name  = "chess-coach"
$repo  = "$owner/$name"
$root  = $PSScriptRoot
$tmp   = Join-Path $env:TEMP "ghbody"
New-Item -ItemType Directory -Force $tmp | Out-Null

# --- repository -------------------------------------------------------------
$check = & $gh repo view $repo --json name 2>&1
if ($LASTEXITCODE -ne 0) {
    & $gh repo create $repo --public --description "Local Stockfish analysis in a floating window for chess.com" 2>&1 | Out-Null
    Write-Host "created repo $repo (exit $LASTEXITCODE)"
} else {
    Write-Host "repo $repo already exists"
}

# --- file list --------------------------------------------------------------
$bat   = Get-ChildItem $root -Filter "*.bat" | Select-Object -First 1
$instr = Get-Item (Join-Path $root "ИНСТРУКЦИЯ-ДЛЯ-ДРУГА.txt") -ErrorAction SilentlyContinue
$zip   = Get-Item (Join-Path $root "Шахматный-тренер.zip") -ErrorAction SilentlyContinue

$items = @(
    @("run.py",                         (Join-Path $root "run.py")),
    @("requirements.txt",               (Join-Path $root "requirements.txt")),
    @(".gitignore",                     (Join-Path $root ".gitignore")),
    @("LICENSE",                        (Join-Path $root "LICENSE")),
    @("README.md",                      (Join-Path $root "README.md")),
    @("build.ps1",                      (Join-Path $root "build.ps1")),
    @("publish.ps1",                    (Join-Path $root "publish.ps1")),
    @($bat.Name,                        $bat.FullName),
    @($instr.Name,                      $instr.FullName),
    @("chess_coach/__init__.py",        (Join-Path $root "chess_coach\__init__.py")),
    @("chess_coach/engine.py",          (Join-Path $root "chess_coach\engine.py")),
    @("chess_coach/panel.py",           (Join-Path $root "chess_coach\panel.py")),
    @("chess_coach/positions.py",       (Join-Path $root "chess_coach\positions.py")),
    @("chess_coach/server.py",          (Join-Path $root "chess_coach\server.py")),
    @("chess_coach/mock_board.html",    (Join-Path $root "chess_coach\mock_board.html")),
    @("userscript/chess-coach.user.js", (Join-Path $root "userscript\chess-coach.user.js")),
    @("docs/panel.png",                 (Join-Path $root "docs\panel.png"))
)

$i = 0
foreach ($item in $items) {
    $rel = $item[0]; $abs = $item[1]; $i++
    if (-not (Test-Path $abs)) { Write-Host "MISSING $rel"; continue }

    $segments = $rel -split "/" | ForEach-Object { [uri]::EscapeDataString($_) }
    $apiPath  = "repos/$repo/contents/" + ($segments -join "/")

    # existing file? keep its sha so the upload updates instead of failing
    $sha = & $gh api $apiPath --jq ".sha" 2>$null
    if ($LASTEXITCODE -ne 0) { $sha = $null }

    $body = @{ message = "update $rel"; content = [Convert]::ToBase64String([IO.File]::ReadAllBytes($abs)) }
    if ($sha) { $body["sha"] = "$sha".Trim() }

    $bodyFile = Join-Path $tmp ("body$i.json")
    [IO.File]::WriteAllText($bodyFile, ($body | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))

    $out = & $gh api --method PUT $apiPath --input $bodyFile 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "FAIL $rel :: $($out -join ' ')" }
    else { Write-Host "ok   $rel" }
}

# --- release ---------------------------------------------------------------
$tag   = "v1.0.0"
$notes = Join-Path $root "release-notes.md"
$asset = Join-Path $tmp "ChessCoach-v1.0.0-win64.zip"   # ASCII name: cyrillic gets mangled in URLs

$existing = & $gh release view $tag --repo $repo --json tagName 2>&1
if ($LASTEXITCODE -ne 0) {
    Copy-Item $zip.FullName $asset -Force
    $out = & $gh release create $tag $asset --repo $repo --title "v1.0.0 - Windows build" --notes-file $notes 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "RELEASE FAIL :: $($out -join ' ')" }
    else { Write-Host "release $tag created" }
} else {
    & $gh release edit $tag --repo $repo --notes-file $notes 2>&1 | Out-Null
    $have = & $gh release view $tag --repo $repo --json assets --jq ".assets[].name" 2>$null
    if ("$have" -notmatch "ChessCoach-v1.0.0-win64.zip") {
        Copy-Item $zip.FullName $asset -Force
        & $gh release upload $tag $asset --repo $repo 2>&1 | Out-Null
        Write-Host "asset uploaded"
    }
    Write-Host "release $tag already exists"
}

Write-Host "DONE: https://github.com/$repo"
