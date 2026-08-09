$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$IntegrationRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$ConnectorRoot = (Resolve-Path -LiteralPath (Join-Path $IntegrationRoot "connector")).Path
$DistRoot = Join-Path $ConnectorRoot "dist"
$BuildRoot = Join-Path $ConnectorRoot "build"
$Exe = Join-Path $DistRoot "superboss.exe"

function Assert-WithinIntegration {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Candidate = [System.IO.Path]::GetFullPath($Path)
    $Boundary = [System.IO.Path]::GetFullPath($IntegrationRoot).TrimEnd("\") + "\"
    if (-not $Candidate.StartsWith($Boundary, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Build path is outside the integration tree."
    }
    return $Candidate
}

function Resolve-Uv {
    if ($env:UV_EXE) {
        $Explicit = [System.IO.Path]::GetFullPath($env:UV_EXE)
        if (Test-Path -LiteralPath $Explicit -PathType Leaf) {
            return $Explicit
        }
    }

    $Command = Get-Command "uv.exe" -ErrorAction SilentlyContinue
    if ($null -ne $Command) {
        return $Command.Source
    }

    $ProfileRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    $LocalCandidate = Join-Path $ProfileRoot ".local\bin\uv.exe"
    if (Test-Path -LiteralPath $LocalCandidate -PathType Leaf) {
        return $LocalCandidate
    }
    throw "A local uv executable is required."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "A required native build gate failed."
    }
}

$DistRoot = Assert-WithinIntegration $DistRoot
$BuildRoot = Assert-WithinIntegration $BuildRoot
$Uv = Resolve-Uv

if (Test-Path -LiteralPath $DistRoot) {
    Remove-Item -LiteralPath $DistRoot -Recurse -Force
}
if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}

Push-Location -LiteralPath $ConnectorRoot
try {
    Invoke-Checked $Uv @("run", "--locked", "--group", "dev", "pytest")
    Invoke-Checked $Uv @("run", "--locked", "--group", "dev", "ruff", "check", ".")
    Invoke-Checked $Uv @("run", "--locked", "--group", "dev", "mypy", "src")
    Invoke-Checked $Uv @("run", "--locked", "--group", "dev", "python", "-m", "build", "--wheel", "--no-isolation", "--outdir", $DistRoot)
    Invoke-Checked $Uv @("run", "--locked", "--group", "dev", "pyinstaller", "--noconfirm", "--clean", "--onefile", "--name", "superboss", "--paths", "src", "--collect-submodules", "keyring.backends", "--distpath", $DistRoot, "--workpath", $BuildRoot, "--specpath", $BuildRoot, "src/superboss_connector/__main__.py")
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
        throw "The expected executable was not produced."
    }
    Invoke-Checked $Exe @("--help")
}
catch {
    if (Test-Path -LiteralPath $DistRoot) {
        Remove-Item -LiteralPath $DistRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $BuildRoot) {
        Remove-Item -LiteralPath $BuildRoot -Recurse -Force
    }
    throw
}
finally {
    Pop-Location
}

if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
