# One-click installer for agent-headless-browser (Windows).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   iwr -useb https://raw.githubusercontent.com/EgiStr/agent-headless-browser/main/scripts/install.ps1 | iex
#
# It finds a Python 3.10+, downloads install.py when it is not next to this
# script, and runs it. With no Python at all it bootstraps uv, which can
# provide one. Any argument is passed straight to install.py.
$ErrorActionPreference = 'Stop'

$Base = if ($env:AHB_INSTALL_BASE_URL) {
    $env:AHB_INSTALL_BASE_URL
} else {
    'https://raw.githubusercontent.com/EgiStr/agent-headless-browser/main/scripts'
}

$here = $PSScriptRoot
if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
$installer = if ($here) { Join-Path $here 'install.py' } else { $null }
if (-not $installer -or -not (Test-Path $installer)) {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('ahb-install-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $installer = Join-Path $tmp 'install.py'
    Write-Host '==> downloading install.py'
    Invoke-WebRequest -UseBasicParsing "$Base/install.py" -OutFile $installer
}

function Test-Python($exe, $prefix) {
    try {
        $out = & $exe @prefix -c "import sys; print('ok' if sys.version_info >= (3, 10) else 'no')" 2>$null
        if ($LASTEXITCODE -eq 0) { return (($out | Out-String).Trim() -eq 'ok') }
    } catch {
        return $false
    }
    return $false
}

$python = $null
$candidates = @()
foreach ($name in @('python3', 'python')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += , @($cmd.Source, @()) }
}
$py = Get-Command 'py' -ErrorAction SilentlyContinue
if ($py) { $candidates += , @($py.Source, @('-3')) }

foreach ($candidate in $candidates) {
    if (Test-Python $candidate[0] $candidate[1]) { $python = $candidate; break }
}

if (-not $python) {
    $uv = Get-Command 'uv' -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Host '==> installing uv (it can provide Python 3.12)'
        Invoke-RestMethod 'https://astral.sh/uv/install.ps1' | Invoke-Expression
        $env:PATH = "$env:USERPROFILE\.local\bin;" + $env:PATH
        $uv = Get-Command 'uv' -ErrorAction SilentlyContinue
    }
    if ($uv) {
        Write-Host '==> no Python 3.10+ found, using uv to provide one'
        & $uv.Source run --no-project --python 3.12 $installer @args
        exit $LASTEXITCODE
    }
    Write-Error 'no Python 3.10+ found and uv could not be installed'
    exit 1
}

& $python[0] @($python[1]) $installer @args
exit $LASTEXITCODE
