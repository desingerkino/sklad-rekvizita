# Сборка .exe и установщика «Склад Реквизита».
# Запускать из корня проекта:  powershell -ExecutionPolicy Bypass -File build\build.ps1
# Или просто двойным щелчком по build\build.bat
#
# ВАЖНО: этот файл сохранён в UTF-8 с BOM. Если открывать его в редакторе и
# сохранять заново — сохраняйте в той же кодировке, иначе Windows PowerShell
# прочитает русские буквы как мусор и откажется запускать сценарий.

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail($message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    Write-Host "Сборка остановлена. Установщик и .exe не создавались." -ForegroundColor Red
    exit 1
}

# --- [1/5] Python -----------------------------------------------------------
Write-Host "[1/5] Проверка Python..." -ForegroundColor Cyan

$pyInfo = & python -c "import sys; print('%d.%d.%d %s' % (sys.version_info[0], sys.version_info[1], sys.version_info[2], sys.version_info.releaselevel))" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $pyInfo) {
    Fail "Python не найден. Установите Python 3.12 с python.org и отметьте галочку `"Add python.exe to PATH`"."
}
$parts   = $pyInfo.Trim().Split(" ")
$pyVer   = $parts[0]
$pyLevel = $parts[1]
$verParts = $pyVer.Split(".")
$major   = [int]($verParts[0])
$minor   = [int]($verParts[1])
Write-Host "    Python $pyVer ($pyLevel)"

if ($pyLevel -ne "final") {
    Fail "Установлена предварительная (alpha/beta) сборка Python $pyVer. Она не годится для сборки: удалите её и поставьте обычный Python 3.12 с python.org."
}
if ($major -ne 3 -or $minor -lt 11 -or $minor -gt 13) {
    Fail "Нужен Python 3.11, 3.12 или 3.13 (найден $pyVer). Рекомендуется 3.12."
}
if ($minor -eq 13) {
    Write-Host "    Внимание: на Python 3.13 библиотеки ставятся более новых версий, чем проверенные. Если что-то пойдёт не так — поставьте Python 3.12." -ForegroundColor Yellow
}

# --- [2/5] Окружение и зависимости ------------------------------------------
Write-Host "[2/5] Виртуальное окружение и зависимости..." -ForegroundColor Cyan

$venvPy = ".\.venv\Scripts\python.exe"
$recreate = $true
if (Test-Path $venvPy) {
    $venvVer = & $venvPy -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $venvVer -and $venvVer.Trim() -eq $pyVer) {
        $recreate = $false
    } else {
        Write-Host "    Старое окружение собрано другим Python — пересоздаю." -ForegroundColor Yellow
    }
}
if ($recreate) {
    Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось создать виртуальное окружение .venv" }
}

& $venvPy -m pip install --upgrade --disable-pip-version-check pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Обновить pip не удалось — пробую работать с тем, что есть." -ForegroundColor Yellow
}
& $venvPy -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Fail "Не удалось установить зависимости. Проверьте подключение к интернету и версию Python (рекомендуется 3.12)."
}

$check = & $venvPy -c "import PySide6, PIL, reportlab, cryptography; print('deps-ok')" 2>&1
if ($LASTEXITCODE -ne 0) { Fail "Библиотеки установились неполностью: $check" }
Write-Host "    Все библиотеки на месте." -ForegroundColor Green

# Библиотеки только для тестов. В .exe они не попадают: PyInstaller собирает
# то, что импортирует main.py. Если их не поставить, часть проверок PDF будет
# пропущена — на сборку это не влияет.
if (Test-Path "requirements-dev.txt") {
    & $venvPy -m pip install --disable-pip-version-check -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    Тестовые библиотеки не установились — часть проверок PDF будет пропущена." -ForegroundColor Yellow
    }
}

# --- [3/5] Тесты ------------------------------------------------------------
Write-Host "[3/5] Тесты бизнес-логики..." -ForegroundColor Cyan

# Наборы тестов. Поле blocking = $false означает: провал этого набора не
# останавливает сборку, а превращается в предупреждение.
#
# Так помечен только тест автосохранения. Он не проверяет бизнес-логику: он
# запускает отдельные процессы и убивает их посреди работы, имитируя снятие
# задачи или пропадание питания. Это единственный набор, который зависит от
# устройства операционной системы (сигналы, буферизация вывода, права на
# завершение процесса), поэтому он способен упасть по причинам, не связанным
# с программой. Всё остальное блокирует сборку по-прежнему: продавать .exe,
# у которого не сходится аренда, нельзя.
$suites = @(
    @{ m = "tests.test_core";       t = "Тесты каталога и аренды";  blocking = $true },
    @{ m = "tests.test_v2";         t = "Тесты версии 2.0";         blocking = $true },
    @{ m = "tests.test_migration";  t = "Тест миграции";            blocking = $true },
    @{ m = "tests.test_durability"; t = "Тест автосохранения";      blocking = $false },
    @{ m = "tests.test_v23";        t = "Тесты версии 2.3";         blocking = $true },
    @{ m = "tests.test_v24";        t = "Тесты версии 2.4";         blocking = $true },
    @{ m = "tests.test_v24_audit";  t = "Тесты инвентаризации";     blocking = $true },
    @{ m = "tests.test_v24_packing"; t = "Тесты комплектации и Excel"; blocking = $true },
    @{ m = "tests.test_v24_backup"; t = "Тесты резервных копий";     blocking = $true },
    @{ m = "tests.test_v24_import"; t = "Тесты импорта из папки";    blocking = $true },
    @{ m = "tests.test_v24_imagesearch"; t = "Тесты поиска по фото";  blocking = $true },
    @{ m = "tests.test_v24_ui_fixes"; t = "Тесты правок интерфейса";  blocking = $true },
    @{ m = "tests.test_v24_calendar"; t = "Тесты календаря аренд";    blocking = $true },
    @{ m = "tests.test_v24_drafts"; t = "Тесты незавершённых аренд";  blocking = $true },
    @{ m = "tests.test_bigdata";    t = "Нагрузочный тест";         blocking = $true },
    @{ m = "tests.test_ui_scroll";  t = "UI QA (скролл и статусы)"; blocking = $true },
    @{ m = "tests.test_security";   t = "Security QA";              blocking = $true },
    @{ m = "tests.test_qa";         t = "Финальный QA-набор";       blocking = $true }
)

$warnings = @()
foreach ($s in $suites) {
    & $venvPy -m $($s.m)
    if ($LASTEXITCODE -ne 0) {
        if ($s.blocking) {
            Fail "$($s.t) не пройден — сборка остановлена."
        }
        $warnings += $s.t
        Write-Host ""
        Write-Host "ВНИМАНИЕ: «$($s.t)» не пройден. Сборка продолжается." -ForegroundColor Yellow
        Write-Host "Этот набор зависит от особенностей Windows и может падать сам по себе." -ForegroundColor Yellow
        Write-Host "Проверки бизнес-логики при этом пройдены." -ForegroundColor Yellow
        Write-Host ""
    }
}

& $venvPy -m tools.make_icons
& $venvPy -m tests.test_ui_static
if ($LASTEXITCODE -ne 0) { Fail "Проверка интерфейса не пройдена — сборка остановлена." }
& $venvPy -m tests.check_imports
if ($LASTEXITCODE -ne 0) { Fail "Проверка модулей не пройдена — сборка остановлена." }

# --- Модель нейросетевого поиска ------------------------------------------
# Если модель лежит в assets\models, она попадает внутрь программы и
# установщика — на новом компьютере поиск по фото работает сразу, без загрузки.
# Файл берётся так:
#   1) уже лежит в assets\models\clip_image.onnx — используем его;
#   2) иначе, если модель скачана через программу на ЭТОМ компьютере
#      (%LOCALAPPDATA%\SkladRekvizita\models), копируем её оттуда;
#   3) иначе собираем без модели — её можно будет скачать из настроек.
$bundledModel = "assets\models\clip_image.onnx"
$downloadedModel = Join-Path $env:LOCALAPPDATA "SkladRekvizita\models\clip_image.onnx"
$minModelBytes = 5MB
if ((Test-Path $bundledModel) -and ((Get-Item $bundledModel).Length -ge $minModelBytes)) {
    $size = [math]::Round((Get-Item $bundledModel).Length / 1MB)
    Write-Host "    Нейросеть: модель уже в assets\models ($size МБ) — войдёт в программу." -ForegroundColor Green
} elseif ((Test-Path $downloadedModel) -and ((Get-Item $downloadedModel).Length -ge $minModelBytes)) {
    New-Item -ItemType Directory -Force -Path "assets\models" | Out-Null
    Copy-Item $downloadedModel $bundledModel -Force
    $size = [math]::Round((Get-Item $bundledModel).Length / 1MB)
    Write-Host "    Нейросеть: взята скачанная модель ($size МБ) — войдёт в программу." -ForegroundColor Green
} else {
    Write-Host "    Нейросеть: модель не найдена, программа соберётся без неё." -ForegroundColor Yellow
    Write-Host "    Чтобы встроить: скачайте модель в программе (Настройки → Поиск по фото)" -ForegroundColor Yellow
    Write-Host "    и запустите сборку ещё раз — файл подхватится сам." -ForegroundColor Yellow
}

# --- [4/5] Сборка .exe ------------------------------------------------------
Write-Host "[4/5] Сборка .exe (PyInstaller)..." -ForegroundColor Cyan
Remove-Item -Recurse -Force "build\temp", "dist" -ErrorAction SilentlyContinue
& ".\.venv\Scripts\pyinstaller.exe" "build\sklad.spec" --noconfirm --workpath "build\temp" --distpath "dist"
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller не смог собрать .exe" }
if (Test-Path $bundledModel) {
    $inside = Get-ChildItem -Path "dist\SkladRekvizita" -Recurse -Filter "clip_image.onnx" -ErrorAction SilentlyContinue
    if ($inside) {
        Write-Host "    Модель внутри программы: $($inside[0].FullName)" -ForegroundColor Green
    } else {
        $warnings += "Модель нейросети не попала в сборку"
        Write-Host "    ВНИМАНИЕ: модель не попала внутрь программы." -ForegroundColor Yellow
    }
}
Write-Host "    Готово: dist\SkladRekvizita\SkladRekvizita.exe" -ForegroundColor Green

# --- [5/5] Установщик -------------------------------------------------------
Write-Host "[5/5] Установщик (Inno Setup)..." -ForegroundColor Cyan
$iscc = @(
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
    & $iscc "build\installer.iss"
    if ($LASTEXITCODE -ne 0) { Fail "Inno Setup не смог собрать установщик." }
    Write-Host "    Установщик: dist\SkladRekvizita_Setup_2.4.exe" -ForegroundColor Green
} else {
    Write-Host "    Inno Setup не найден. Установите его с https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
    Write-Host "    и повторите шаг: ISCC.exe build\installer.iss" -ForegroundColor Yellow
}

Write-Host ""
if ($warnings.Count -gt 0) {
    Write-Host "Сборка завершена С ПРЕДУПРЕЖДЕНИЯМИ." -ForegroundColor Yellow
    foreach ($w in $warnings) { Write-Host "  — не пройден набор: $w" -ForegroundColor Yellow }
    Write-Host "Программа собрана, но эти проверки стоит разобрать." -ForegroundColor Yellow
} else {
    Write-Host "Сборка завершена. Все проверки пройдены." -ForegroundColor Green
}
