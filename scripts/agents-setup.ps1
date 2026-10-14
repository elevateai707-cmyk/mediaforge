<#
.SYNOPSIS
  MediaForge AI agent setup for a Windows desktop (idempotent, no admin needed).

.DESCRIPTION
  1. Installs photocraft-cli from the official storytold/photocraft GitHub release
     (portable zip), verified against the release's SHA256SUMS.txt, into
     %LOCALAPPDATA%\Programs\PhotoCraft\<tag>.
  2. Registers the `mediaforge` and `photocraft` MCP servers, plus the mediaforge
     skill, with Claude Code and Codex (agents\register_agents.py). Hermes Agent
     doesn't run natively on Windows: install it in WSL2 and run
     scripts/agents-setup.sh there.

  MediaForge itself usually runs on the Ubuntu box. Point -MediaForgeUrl at it,
  e.g. http://192.168.1.50:8420 (scripts/run.sh prints the LAN URL).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\agents-setup.ps1 -MediaForgeUrl http://192.168.1.50:8420
#>
[CmdletBinding()]
param(
  [string]$MediaForgeUrl = $(if ($env:MEDIAFORGE_URL) { $env:MEDIAFORGE_URL } else { 'http://127.0.0.1:8420' }),
  [string]$PhotoCraftVersion = 'latest',
  [string]$ReadRoot = (Join-Path $env:USERPROFILE 'Pictures'),
  [string]$WriteRoot = (Join-Path $env:USERPROFILE 'Pictures\PhotoCraft-Agent'),
  [string]$Agents = 'claude,codex,hermes',
  [switch]$CanApprove,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is 10x faster without it
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = 'storytold/photocraft'
$MfRoot = Split-Path -Parent $PSScriptRoot
$InstallRoot = Join-Path $env:LOCALAPPDATA 'Programs\PhotoCraft'

function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Info($m) { Write-Host "  ->  $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [x] $m" -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host '================================================================'
Write-Host '  MediaForge agents setup (Claude Code / Codex / Hermes)'
Write-Host '================================================================'
Write-Host ''

# --- Python 3.10+ (real interpreter path, not the Microsoft Store stub) --------
$Python = $null
foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
  $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
  if (-not $exe) { continue }
  try {
    $pyArgs = @($cand | Select-Object -Skip 1) + @('-c', 'import sys; assert sys.version_info >= (3, 10); print(sys.executable)')
    $out = & $exe.Source @pyArgs 2>$null
    if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path $out)) { $Python = "$out".Trim(); break }
  } catch { }
}
if (-not $Python) { Fail 'Python 3.10+ is required: winget install Python.Python.3.12' }
Ok "python: $Python"

# --- 1. photocraft-cli ---------------------------------------------------------
switch ($env:PROCESSOR_ARCHITECTURE) {
  'AMD64' { $Arch = 'x64' }
  'ARM64' { $Arch = 'arm64' }
  'x86'   { $Arch = 'x86' }
  default { Fail "no PhotoCraft Windows build for $($env:PROCESSOR_ARCHITECTURE)" }
}

$PcCli = Get-ChildItem -Path $InstallRoot -Recurse -Filter 'photocraft-cli.exe' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if ($PcCli -and $PhotoCraftVersion -eq 'latest' -and -not $Force) {
  Ok "already installed: $(& $PcCli --version) (-Force to update)"
} else {
  if ($PhotoCraftVersion -eq 'latest') {
    $Tag = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ 'User-Agent' = 'mediaforge-setup' }).tag_name
  } else { $Tag = $PhotoCraftVersion }
  Info "PhotoCraft release $Tag ($Arch)"
  $Base = "https://github.com/$Repo/releases/download/$Tag"
  $Tmp = Join-Path ([IO.Path]::GetTempPath()) ("pc-" + [guid]::NewGuid())
  New-Item -ItemType Directory -Path $Tmp | Out-Null
  try {
    $SumsPath = Join-Path $Tmp 'SHA256SUMS.txt'
    Invoke-WebRequest "$Base/SHA256SUMS.txt" -OutFile $SumsPath -UseBasicParsing
    $Line = Get-Content $SumsPath | Where-Object { $_ -match "^([0-9a-fA-F]{64})\s+\*?(photocraft-\S+-windows-$Arch-portable\.zip)$" } | Select-Object -First 1
    if (-not $Line) { Fail "no windows-$Arch portable zip listed in $Tag's SHA256SUMS.txt" }
    $null = $Line -match "^([0-9a-fA-F]{64})\s+\*?(\S+)$"
    $Expected = $Matches[1].ToLower(); $Asset = $Matches[2]

    Info "downloading $Asset"
    $Zip = Join-Path $Tmp $Asset
    Invoke-WebRequest "$Base/$Asset" -OutFile $Zip -UseBasicParsing
    $Actual = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
    if ($Actual -ne $Expected) { Fail "SHA-256 mismatch for $Asset; refusing to install" }
    Ok "checksum verified ($Asset)"

    $Dest = Join-Path $InstallRoot $Tag
    if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    Expand-Archive -Path $Zip -DestinationPath $Dest -Force
    $PcCli = Get-ChildItem -Path $Dest -Recurse -Filter 'photocraft-cli.exe' | Select-Object -First 1 -ExpandProperty FullName
    if (-not $PcCli) { Fail "photocraft-cli.exe not found inside $Asset" }
    Ok "installed $(& $PcCli --version) -> $PcCli"
  } finally {
    Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}

# Put PhotoCraft on the user PATH for convenience (agents get absolute paths anyway).
$PcDir = Split-Path -Parent $PcCli
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($UserPath -split ';') -contains $PcDir)) {
  $Kept = ($UserPath -split ';') | Where-Object { $_ -and ($_ -notlike "$InstallRoot*") }
  [Environment]::SetEnvironmentVariable('Path', (($Kept + $PcDir) -join ';'), 'User')
  Ok "added $PcDir to your PATH (new terminals)"
}

# --- 2. register with the agents ----------------------------------------------
New-Item -ItemType Directory -Path $WriteRoot -Force | Out-Null
$RegArgs = @(
  (Join-Path $MfRoot 'agents\register_agents.py'),
  '--url', $MediaForgeUrl, '--python', $Python, '--agents', $Agents,
  '--photocraft-cli', $PcCli, '--pc-write-root', $WriteRoot
)
if (Test-Path $ReadRoot) {
  $RegArgs += @('--pc-read-root', $ReadRoot)
  Info "photocraft MCP may read $ReadRoot and write $WriteRoot only"
} else {
  Warn "$ReadRoot doesn't exist; photocraft MCP gets no read access (-ReadRoot)"
}
if ($CanApprove) { $RegArgs += '--can-approve' }
& $Python @RegArgs
if ($LASTEXITCODE -ne 0) { Fail 'agent registration failed' }

# --- 3. reachability -----------------------------------------------------------
try {
  $null = Invoke-RestMethod "$MediaForgeUrl/api/health" -TimeoutSec 4
  Ok "MediaForge reachable at $MediaForgeUrl"
} catch {
  Warn "MediaForge not reachable at $MediaForgeUrl. Is ./scripts/run.sh running on the Ubuntu box, and is port 8420 open (sudo ufw allow 8420/tcp)?"
}

Write-Host ''
Write-Host '  Try it:'
Write-Host '    claude  -> "/mcp__mediaforge__library_report" or "make a 30s TikTok of my last trip"'
Write-Host '    codex   -> "use mediaforge to find my best beach photos and give them the film look"'
Write-Host '    hermes  -> (in WSL2) "what trips are in my MediaForge library?"'
Write-Host ''
