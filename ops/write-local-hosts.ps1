#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$log = Join-Path $env:TEMP "superboss-hosts-result.txt"
function Write-Log([string]$message) {
    $message | Out-File -FilePath $log -Encoding utf8 -Append
}
Set-Content -Path $log -Value "write-local-hosts start $(Get-Date -Format o)" -Encoding utf8

$path = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$needed = @(
    "127.0.0.1 app.localhost",
    "127.0.0.1 objects.localhost"
)

$raw = [System.IO.File]::ReadAllText($path)
$added = New-Object System.Collections.Generic.List[string]
foreach ($line in $needed) {
    $name = ($line -split "\s+")[-1]
    $pattern = "(?im)^\s*127\.0\.0\.1\s+$([regex]::Escape($name))(\s|$)"
    if ($raw -notmatch $pattern) {
        $added.Add($line)
    }
}

if ($added.Count -eq 0) {
    Write-Log "hosts already contains app.localhost and objects.localhost"
    Write-Output "hosts already contains app.localhost and objects.localhost"
    exit 0
}

$block = New-Object System.Text.StringBuilder
if ($raw.Length -gt 0 -and -not $raw.EndsWith("`n")) {
    [void]$block.Append("`r`n")
}
[void]$block.Append("`r`n# SuperBoss local HTTPS`r`n")
foreach ($line in $added) {
    [void]$block.Append($line)
    [void]$block.Append("`r`n")
}

$stream = [System.IO.File]::Open(
    $path,
    [System.IO.FileMode]::Append,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::Read
)
try {
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($block.ToString())
    $stream.Write($bytes, 0, $bytes.Length)
}
finally {
    $stream.Close()
}

ipconfig /flushdns | Out-Null
$summary = "wrote: " + ($added -join ", ")
Write-Log $summary
Write-Output $summary
