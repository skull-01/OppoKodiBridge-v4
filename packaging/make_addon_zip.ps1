# Build dist/service.oppokodibridge-<version>.zip for Kodi install.
#
# The zip contains the add-on under a top-level folder named exactly the add-on id, with
# FORWARD-SLASH path separators. PowerShell 5.1's Compress-Archive writes BACKSLASH separators,
# which are not spec-correct and break extraction on Linux/CoreELEC -- both Kodi's "install from
# zip file" and an SSH-side unzip see "service.oppokodibridge\addon.xml" as one literal filename
# rather than a folder. So we build the entries by hand with correct arcnames.
param([string]$Version)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "service.oppokodibridge.v4"
if (-not (Test-Path $src)) { throw "add-on folder not found: $src" }

if (-not $Version) {
    [xml]$x = Get-Content (Join-Path $src "addon.xml")
    $Version = $x.addon.version
}

$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$zipPath = Join-Path $dist "service.oppokodibridge.v4-$Version.zip"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

Add-Type -AssemblyName System.IO.Compression | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null

# Files to ship: the whole add-on tree minus dev cruft.
$files = Get-ChildItem -Recurse -File -LiteralPath $src | Where-Object {
    $_.FullName -notmatch '\\__pycache__\\' -and $_.Extension -ne '.pyc'
} | Sort-Object FullName

# Arcnames are relative to the add-on's PARENT, so each entry starts "service.oppokodibridge/...".
$srcParent = Split-Path -Parent $src
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($srcParent.Length + 1).Replace('\', '/')
        $entry = $zip.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
        $out = $entry.Open()
        try {
            $in = [System.IO.File]::OpenRead($f.FullName)
            try { $in.CopyTo($out) } finally { $in.Dispose() }
        } finally { $out.Dispose() }
    }
} finally {
    $zip.Dispose()
}
Write-Host "Built $zipPath ($($files.Count) files, forward-slash entries)"
