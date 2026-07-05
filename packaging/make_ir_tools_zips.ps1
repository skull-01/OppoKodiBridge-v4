# Build the two IR bench/dev-tool bundles for a release:
#   dist/oppo-ir-tools-windows-zjiot-<version>.zip   (Windows ZJIoT-serial console)
#   dist/oppo-ir-tools-rpi4-lirc-<version>.zip       (Raspberry Pi 4 LIRC console)
#
# These are DEV tools shipped as separate release downloads, NOT part of the add-on. Each bundle is a
# self-contained, flattened subset of tools/: the console entry-point plus only the packages it imports,
# with FORWARD-SLASH arcnames and no __pycache__/.pyc (same rationale as make_addon_zip.ps1 -- PowerShell
# 5.1's Compress-Archive writes backslashes, which break extraction on Linux/CoreELEC).
#
# Arcnames are relative to tools/, so tools/ir/proto.py ships as ir/proto.py. Keep the two file lists
# below in lockstep with the consoles' imports when a tool grows a new dependency.
param([string]$Version)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $root "tools"
if (-not (Test-Path $tools)) { throw "tools folder not found: $tools" }

if (-not $Version) {
    [xml]$x = Get-Content (Join-Path $root "service.oppokodibridge.v4/addon.xml")
    $Version = $x.addon.version
}

# arcname (relative to tools/, forward slashes) -> the files each bundle ships.
$windowsZjiot = @(
    "zjiot_console.py",
    "ir/__init__.py", "ir/proto.py", "ir/codes.py", "ir/serial_win.py", "ir/tkutil.py",
    "README_ZJIOT_CONSOLE.md", "requirements-dev.txt"
)
$rpi4Lirc = @(
    "lirc_console.py", "setup_rpi4_lirc.py",
    "ir/__init__.py", "ir/codes.py",
    "lirc/__init__.py", "lirc/ctl.py", "lirc/devices.py",
    "README_LIRC_CONSOLE.md"
)

$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

Add-Type -AssemblyName System.IO.Compression | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null

function Build-Bundle([string]$zipName, [string[]]$arcnames) {
    $zipPath = Join-Path $dist $zipName
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($arc in $arcnames) {
            $srcFile = Join-Path $tools ($arc -replace '/', '\')
            if (-not (Test-Path -LiteralPath $srcFile)) { throw "missing bundle file: $srcFile" }
            $entry = $zip.CreateEntry($arc, [System.IO.Compression.CompressionLevel]::Optimal)
            $out = $entry.Open()
            try {
                $in = [System.IO.File]::OpenRead($srcFile)
                try { $in.CopyTo($out) } finally { $in.Dispose() }
            } finally { $out.Dispose() }
        }
    } finally {
        $zip.Dispose()
    }
    Write-Host "Built $zipPath ($($arcnames.Count) files, forward-slash entries)"
}

Build-Bundle "oppo-ir-tools-windows-zjiot-$Version.zip" $windowsZjiot
Build-Bundle "oppo-ir-tools-rpi4-lirc-$Version.zip" $rpi4Lirc
