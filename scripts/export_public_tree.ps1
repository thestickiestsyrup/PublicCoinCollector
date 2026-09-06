# Export a clean public tree (no private data, no git history).
param(
    [Parameter(Mandatory = $true)]
    [string]$Dest
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Dest = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Dest)

if (Test-Path $Dest) {
    throw "Destination already exists: $Dest"
}
New-Item -ItemType Directory -Path $Dest | Out-Null

$excludeDirs = @(
    ".git", ".venv", "__pycache__", "coin_tray_backups", "samples",
    ".idea", ".vscode", "build", ".gradle"
)
$excludeFiles = @(
    "coin_tray_data.json", "coin_tray_config.json", "local.properties",
    "_ssl_probe.py", "_bing_slice.html"
)

function ShouldSkip($full) {
    $rel = $full.Substring($Root.Path.Length).TrimStart("\", "/")
    foreach ($d in $excludeDirs) {
        if ($rel -eq $d -or $rel.StartsWith("$d\") -or $rel.Contains("\$d\")) {
            return $true
        }
    }
    $name = Split-Path $full -Leaf
    if ($excludeFiles -contains $name) { return $true }
    if ($name -like "_dbg_*.py" -or $name -like "_test_*.py") { return $true }
    if ($name -like "*.log" -or $name -like "*.apk" -or $name -like "*.aab") { return $true }
    if ($name -eq "local.properties") { return $true }
    return $false
}

Get-ChildItem -Path $Root -Recurse -Force | ForEach-Object {
    if ($_.PSIsContainer) { return }
    $src = $_.FullName
    if (ShouldSkip $src) { return }
    $rel = $src.Substring($Root.Path.Length).TrimStart("\", "/")
    $target = Join-Path $Dest $rel
    $dir = Split-Path $target -Parent
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    Copy-Item -Path $src -Destination $target -Force
}

Write-Host "Exported clean tree to $Dest"
Write-Host "Next: cd there, git init -b main, review, commit, push to new public GitHub."
