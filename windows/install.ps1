<#
.SYNOPSIS
  Build the FaderDisplay Stream Deck plugin and install it into the Stream Deck app.
.DESCRIPTION
  Safe to re-run. Quits the Stream Deck app (it holds the plugin exe open), copies the
  plugin into %APPDATA%\Elgato\StreamDeck\Plugins, then starts the app again.
  Needs the .NET 10 SDK to build; the plugin itself needs only the .NET 10 runtime.
#>
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$plugin = 'com.marc.faderdisplay.sdPlugin'
$target = Join-Path $env:APPDATA "Elgato\StreamDeck\Plugins\$plugin"
$app = Join-Path $env:ProgramFiles 'Elgato\StreamDeck\StreamDeck.exe'

Write-Host '==> Building'
dotnet publish src/FaderDisplay -c Release -o "$plugin/bin" --nologo
if ($LASTEXITCODE -ne 0) { throw 'build failed' }

Write-Host '==> Stopping Stream Deck'
$running = Get-Process StreamDeck -ErrorAction SilentlyContinue
if ($running) {
    $running | Stop-Process
    $running | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue
}
Get-Process FaderDisplay -ErrorAction SilentlyContinue | Stop-Process -ErrorAction SilentlyContinue

Write-Host "==> Installing to $target"
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Copy-Item $plugin $target -Recurse

if (Test-Path $app) {
    Write-Host '==> Starting Stream Deck'
    Start-Process $app
} else {
    Write-Warning "Stream Deck app not found at $app - install it from https://www.elgato.com/downloads"
}

Write-Host @'

Done. In the Stream Deck app, drag "Mixer Key" (FaderDisplay category) onto every key
of a profile; each key's role follows from its position. Optional config:
'@
Write-Host "  $env:APPDATA\FaderDisplay\fader-display.json  (example: fader-display.example.json)"
