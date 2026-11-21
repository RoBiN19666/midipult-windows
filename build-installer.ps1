# Сборка установщика MidiPult через Inno Setup 7.
# Использование:
#   .\build-installer.ps1          — собрать dist\MidiPult-<версия>-setup.exe
#   .\build-installer.ps1 -Test    — собрать и проверить установку/удаление
#   .\build-installer.ps1 -KeepBuild — не удалять временные файлы Inno

param(
    [switch]$Test,
    [switch]$KeepBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$exe = Join-Path $root 'dist\MidiPult.exe'
if (-not (Test-Path $exe)) {
    Write-Host 'MidiPult.exe не найден, собираю...' -ForegroundColor Yellow
    & (Join-Path $root 'build.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'Не удалось собрать MidiPult.exe'
    }
}

# Версии в app.py и в скрипте установщика должны совпадать.
$appVersion = (Select-String -Path (Join-Path $root 'app.py') -Pattern 'APP_VERSION = "([^"]+)"').Matches[0].Groups[1].Value
$issVersion = (Select-String -Path (Join-Path $root 'installer\midipult.iss') -Pattern '#define MyAppVersion "([^"]+)"').Matches[0].Groups[1].Value
if ($appVersion -ne $issVersion) {
    throw "Версия в app.py ($appVersion) не совпадает с версией в installer\midipult.iss ($issVersion)"
}

function Find-ISCC {
    if ($env:INNOSETUP) {
        $candidate = if (Test-Path $env:INNOSETUP -PathType Leaf) { $env:INNOSETUP } else { Join-Path $env:INNOSETUP 'ISCC.exe' }
        if (Test-Path $candidate) { return $candidate }
    }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($programFiles in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        if (-not $programFiles) { continue }
        foreach ($version in @('Inno Setup 7', 'Inno Setup 6')) {
            $candidate = Join-Path $programFiles (Join-Path $version 'ISCC.exe')
            if (Test-Path $candidate) { return $candidate }
        }
    }
    $local = Join-Path $root 'tools\innosetup\ISCC.exe'
    if (Test-Path $local) { return $local }
    $cache = Join-Path $env:LOCALAPPDATA 'MidiPult\tools\innosetup\ISCC.exe'
    if (Test-Path $cache) { return $cache }
    return $null
}

$iscc = Find-ISCC
if (-not $iscc) {
    Write-Host 'Inno Setup не найден, скачиваю...' -ForegroundColor Yellow
    $tools = Join-Path $env:LOCALAPPDATA 'MidiPult\tools'
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    $setup = Join-Path $tools 'innosetup-setup.exe'
    $target = Join-Path $tools 'innosetup'
    Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-7_1_1/innosetup-7.1.1-x64.exe' -OutFile $setup -UseBasicParsing
    $proc = Start-Process -FilePath $setup -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', "/DIR=`"$target`""
    ) -Wait -PassThru
    if ($proc.ExitCode -ne 0 -or -not (Test-Path (Join-Path $target 'ISCC.exe'))) {
        throw "Не удалось установить Inno Setup (код $($proc.ExitCode))"
    }
    $iscc = Join-Path $target 'ISCC.exe'
}
Write-Host "Inno Setup: $iscc" -ForegroundColor Cyan

$setupExe = Join-Path $root "dist\MidiPult-$appVersion-setup.exe"

$ErrorActionPreference = 'Continue'
& $iscc (Join-Path $root 'installer\midipult.iss')
$isccCode = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($isccCode -ne 0) {
    throw "ISCC завершился с кодом $isccCode"
}
if (-not (Test-Path $setupExe)) {
    throw "Сборка не создала $setupExe"
}

$hash = (Get-FileHash $setupExe -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$setupExe.sha256" -Value "$hash  $(Split-Path $setupExe -Leaf)" -Encoding ascii
$size = [math]::Round((Get-Item $setupExe).Length / 1MB, 1)
Write-Host "Готово: $setupExe ($size МБ)" -ForegroundColor Green
Write-Host "SHA-256: $hash"

if ($Test) {
    $appExe = Join-Path $env:LOCALAPPDATA 'Programs\MidiPult\MidiPult.exe'
    $startLnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\MidiPult\MidiPult.lnk'
    $desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'MidiPult.lnk'

    if (Get-Process -Name MidiPult -ErrorAction SilentlyContinue) {
        throw 'Закройте запущенный MidiPult перед проверкой установщика'
    }

    Write-Host 'Проверяю установку...' -ForegroundColor Cyan
    Start-Process -FilePath $setupExe -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=desktopicon') -Wait

    $problems = @()
    if (-not (Test-Path $appExe)) { $problems += "нет файла $appExe" }
    if (-not (Test-Path $startLnk)) { $problems += "нет ярлыка $startLnk" }
    if (-not (Test-Path $desktopLnk)) { $problems += "нет ярлыка $desktopLnk" }

    $arp = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ItemProperty $_.PSPath } |
        Where-Object { $_.DisplayName -like '*MidiPult*' }
    if (-not $arp) { $problems += 'нет записи об удалении в реестре (HKCU)' }

    if ($problems.Count) {
        throw "Установка прошла не полностью: $($problems -join '; ')"
    }
    Write-Host 'Установка прошла, ярлыки и запись об удалении на месте.' -ForegroundColor Green

    Write-Host 'Проверяю запуск установленного приложения...' -ForegroundColor Cyan
    $proc = Start-Process -FilePath $appExe -PassThru
    Start-Sleep -Seconds 6
    $alive = -not $proc.HasExited
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Get-Process -Name MidiPult -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    if (-not $alive) { throw 'Установленное приложение не запустилось' }
    Write-Host 'Приложение запускается.' -ForegroundColor Green

    Write-Host 'Проверяю удаление...' -ForegroundColor Cyan
    $unins = Join-Path $env:LOCALAPPDATA 'Programs\MidiPult\unins000.exe'
    if (-not (Test-Path $unins)) { throw "Нет $unins" }
    Start-Process -FilePath $unins -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait

    $left = @()
    if (Test-Path $appExe) { $left += 'файл приложения' }
    if (Test-Path $startLnk) { $left += 'ярлык в меню Пуск' }
    if (Test-Path $desktopLnk) { $left += 'ярлык на рабочем столе' }
    $arpStill = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ItemProperty $_.PSPath } |
        Where-Object { $_.DisplayName -like '*MidiPult*' }
    if ($arpStill) { $left += 'запись в реестре' }
    if ($left.Count) {
        throw "После удаления остались: $($left -join ', ')"
    }
    Write-Host 'Удаление прошло полностью.' -ForegroundColor Green
}

if (-not $KeepBuild) {
    Remove-Item (Join-Path $root 'installer\Output') -Recurse -Force -ErrorAction SilentlyContinue
}
