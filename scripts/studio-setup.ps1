<#
.SYNOPSIS
  Elevate Vision Studio: build (or install a prebuilt zip) on Windows. Idempotent, no admin
  needed except the one-time Visual Studio Build Tools install.

.DESCRIPTION
  Elevate Vision Studio is Elevate Vision Digital's build of FilmCraft. Two ways in:

  * -ZipPath <evstudio-windows-x64.zip>  install a prebuilt zip (from this repo's
    "Studio build" GitHub Actions run). Fast; checks the zip's .sha256 when it's next to it.
  * default: build from source. Fetches FilmCraft at the commit pinned in
    studio\brand.json, applies studio\rebrand.py, and runs cargo. It needs Git, Python 3.10+,
    Rust (rustup) and the MSVC C++ build tools; the script offers to install the missing ones
    with winget. The first build takes 15-30 min.

  Installs to %LOCALAPPDATA%\Programs\ElevateVisionStudio\<commit>, adds it to your PATH,
  and creates a Start Menu shortcut. Point MediaForge agents at it with -RegisterAgentMcp
  (off by default: FilmCraft's MCP server is not folder-sandboxed).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\studio-setup.ps1
  powershell -ExecutionPolicy Bypass -File scripts\studio-setup.ps1 -ZipPath $HOME\Downloads\evstudio-windows-x64.zip
#>
[CmdletBinding()]
param(
  [string]$ZipPath,
  [string]$Cache = (Join-Path $env:LOCALAPPDATA 'ElevateVisionStudio\cache'),
  [switch]$RegisterAgentMcp,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Info($m) { Write-Host "  ->  $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [x] $m" -ForegroundColor Red; exit 1 }
function Need($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Native($what, [scriptblock]$block) {
  & $block
  if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE)" }
}

$MfRoot = Split-Path -Parent $PSScriptRoot
$Brand = Get-Content (Join-Path $MfRoot 'studio\brand.json') -Raw | ConvertFrom-Json
$Commit = $Brand.upstream.commit
$Short = $Commit.Substring(0, 12)
$Root = Join-Path $env:LOCALAPPDATA 'Programs\ElevateVisionStudio'
$Dest = Join-Path $Root $Short
$Exe = "$($Brand.binary).exe"; $Cli = "$($Brand.cli).exe"

Write-Host ''
Write-Host '================================================================'
Write-Host "  $($Brand.product) - install"
Write-Host "  based on $($Brand.upstream.repo) @ $Short"
Write-Host '================================================================'
Write-Host ''

if ((Test-Path (Join-Path $Dest $Cli)) -and -not $Force -and -not $ZipPath) {
  Ok "already installed: $(& (Join-Path $Dest $Cli) --version) ($Dest; -Force to rebuild)"
}
elseif ($ZipPath) {
  # --- prebuilt zip ---------------------------------------------------------------
  if (-not (Test-Path $ZipPath)) { Fail "no such file: $ZipPath" }
  $Sum = "$ZipPath.sha256"
  if (Test-Path $Sum) {
    $Expected = ((Get-Content $Sum -Raw).Trim() -split '\s+')[0].ToLower()
    $Actual = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()
    if ($Actual -ne $Expected) { Fail "SHA-256 mismatch for $ZipPath; refusing to install" }
    Ok 'checksum verified'
  } else { Warn "no $Sum next to the zip; skipping checksum check" }
  if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
  New-Item -ItemType Directory -Path $Dest -Force | Out-Null
  Expand-Archive -Path $ZipPath -DestinationPath $Dest -Force
  $Found = Get-ChildItem $Dest -Recurse -Filter $Cli | Select-Object -First 1
  if (-not $Found) { Fail "$Cli not found in the zip" }
  if ($Found.DirectoryName -ne $Dest) {
    $Inner = $Found.DirectoryName
    Get-ChildItem $Inner | Move-Item -Destination $Dest -Force
    Remove-Item $Inner -Recurse -Force
  }
  Ok "installed $(& (Join-Path $Dest $Cli) --version) -> $Dest"
}
else {
  # --- build from source ---------------------------------------------------------
  if (-not (Need git)) { Info 'installing Git'; Native 'winget git' { winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements } }
  $Python = $null
  foreach ($cand in @(@('py', '-3'), @('python'))) {
    if (-not (Need $cand[0])) { continue }
    $pyArgs = @($cand | Select-Object -Skip 1) + @('-c', 'import sys; assert sys.version_info >= (3, 10); print(sys.executable)')
    $out = & $cand[0] @pyArgs 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { $Python = "$out".Trim(); break }
  }
  if (-not $Python) { Fail 'Python 3.10+ is required: winget install Python.Python.3.12' }

  $CargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
  if (Test-Path $CargoBin) { $env:Path = "$CargoBin;$env:Path" }
  if (-not (Need cargo)) {
    Info 'installing Rust (rustup)'
    Native 'winget rustup' { winget install -e --id Rustlang.Rustup --accept-source-agreements --accept-package-agreements }
    $env:Path = "$CargoBin;$env:Path"
  }
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  $HasMsvc = (Test-Path $vswhere) -and (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath)
  if (-not $HasMsvc) {
    Info 'installing Visual Studio C++ Build Tools (one-time, asks for admin)'
    Native 'winget buildtools' { winget install -e --id Microsoft.VisualStudio.2022.BuildTools --accept-source-agreements --accept-package-agreements --override '--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended' }
  }
  Native 'rustup' { rustup default stable }
  Ok "$(rustc --version)"

  $Src = Join-Path $Cache 'upstream'
  New-Item -ItemType Directory -Path $Cache -Force | Out-Null
  if (-not (Test-Path (Join-Path $Src '.git'))) {
    Native 'git init' { git init -q $Src }
    Native 'git remote' { git -C $Src remote add origin $Brand.upstream.repo }
  }
  $Head = (git -C $Src rev-parse -q --verify HEAD 2>$null)
  if ($Head -ne $Commit) {
    Info "fetching FilmCraft $Short"
    $env:GIT_LFS_SKIP_SMUDGE = '1'
    Native 'git fetch' { git -C $Src fetch -q --depth 1 origin $Commit }
    Native 'git checkout' { git -C $Src -c advice.detachedHead=false checkout -q --force FETCH_HEAD }
  }
  if ((git -C $Src rev-parse HEAD) -ne $Commit) { Fail "fetched commit doesn't match brand.json" }
  Ok "source at $Short"

  $Tree = Join-Path $Cache 'build'
  if (Test-Path $Tree) { Remove-Item $Tree -Recurse -Force }
  Copy-Item $Src $Tree -Recurse
  Native 'rebrand' { & $Python (Join-Path $MfRoot 'studio\rebrand.py') $Tree }
  Ok "rebranded as $($Brand.product)"

  Info 'building (first time: 15-30 min)...'
  $env:CARGO_TARGET_DIR = Join-Path $Cache 'target'
  Push-Location $Tree
  try { Native 'cargo build' { cargo build --release --locked -p filmcraft -p filmcraft-cli } } finally { Pop-Location }
  $Out = Join-Path $env:CARGO_TARGET_DIR 'release'

  if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
  New-Item -ItemType Directory -Path $Dest -Force | Out-Null
  foreach ($f in @($Exe, $Cli)) { Copy-Item (Join-Path $Out $f) $Dest }
  foreach ($f in @('NOTICE', 'LICENSE-MIT', 'LICENSE-APACHE', 'ATTRIBUTION.md')) { Copy-Item (Join-Path $Tree $f) $Dest }
  Ok "installed $(& (Join-Path $Dest $Cli) --version) -> $Dest"
}

# --- PATH, icon, Start Menu shortcut ------------------------------------------------
Copy-Item (Join-Path $MfRoot 'studio\assets\icon.ico') (Join-Path $Dest 'evstudio.ico') -Force
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$Parts = @($UserPath -split ';' | Where-Object { $_ -and ($_ -notlike "$Root*") })
if (-not ($Parts -contains $Dest)) {
  [Environment]::SetEnvironmentVariable('Path', (($Parts + $Dest) -join ';'), 'User')
  Ok "added $Dest to your PATH (new terminals)"
}
if ($IsWindows -or $env:OS -eq 'Windows_NT') {
  $Lnk = Join-Path ([Environment]::GetFolderPath('Programs')) "$($Brand.product).lnk"
  $Shell = New-Object -ComObject WScript.Shell
  $S = $Shell.CreateShortcut($Lnk)
  $S.TargetPath = Join-Path $Dest $Exe
  $S.IconLocation = Join-Path $Dest 'evstudio.ico'
  $S.Description = "$($Brand.product) by $($Brand.company)"
  $S.Save()
  Ok "Start Menu: $($Brand.product)"
}

if ($RegisterAgentMcp) {
  Warn "registering raw $Cli MCP: it is NOT folder-sandboxed (reads/writes anywhere you can)"
  $Py = (Get-Command py -ErrorAction SilentlyContinue)
  $PyExe = if ($Py) { 'py' } else { 'python' }
  & $PyExe (Join-Path $MfRoot 'agents\register_agents.py') --studio-cli (Join-Path $Dest $Cli) --studio-only
}

Write-Host ''
Write-Host "  Open MediaForge projects: $($Brand.binary) <path>\<plan>.fcproj"
Write-Host '  (on the Ubuntu box they are under exports\filmcraft\<plan>\; copy or share the folder)'
Write-Host ''
