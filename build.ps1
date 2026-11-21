# Сборка MidiPult.exe через PyInstaller.
# Использование: .\build.ps1
#               .\build.ps1 -OneDir   (папка вместо одного exe — быстрее запуск)

param(
    [switch]$OneDir
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m pip install --disable-pip-version-check -r requirements-dev.txt

$common = @(
    '--noconfirm',
    '--clean',
    '--windowed',
    '--name', 'MidiPult',
    '--icon', 'assets/icon.ico',
    '--add-data', 'web;web',
    '--add-data', 'assets/icon.ico;assets',
    '--version-file', 'version_info.txt',
    'app.py'
)

if ($OneDir) {
    $pyArgs = @('--onedir') + $common
} else {
    $pyArgs = @('--onefile') + $common
}

Write-Host "Сборка: pyinstaller $($pyArgs -join ' ')" -ForegroundColor Cyan
# PyInstaller пишет INFO в stderr; не превращаем это в ошибку PowerShell.
$ErrorActionPreference = 'Continue'
python -m PyInstaller @pyArgs
$buildCode = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($buildCode -ne 0) {
    throw "PyInstaller завершился с кодом $buildCode"
}

$target = if ($OneDir) { 'dist\MidiPult\MidiPult.exe' } else { 'dist\MidiPult.exe' }
if (-not (Test-Path $target)) {
    throw "Сборка не создала $target"
}

$hash = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$target.sha256" -Value "$hash  $(Split-Path $target -Leaf)" -Encoding ascii

$size = [math]::Round((Get-Item $target).Length / 1MB, 1)
Write-Host "Готово: $target ($size МБ)" -ForegroundColor Green
Write-Host "SHA-256: $hash"
