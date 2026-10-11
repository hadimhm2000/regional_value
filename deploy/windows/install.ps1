# ═══════════════════════════════════════════════════════════════
# نصب ربات ارزش منطقه‌ای روی ویندوز سرور، کنار ربات اصلی (C:\apps\judicial-bot)
#
# PowerShell را «Run as Administrator» باز کنید و بزنید:
#   powershell -ExecutionPolicy Bypass -File C:\apps\regional_value\deploy\windows\install.ps1
#
# اجرای دوباره امن است (تعمیر/به‌روزرسانی نصب).
# ═══════════════════════════════════════════════════════════════
. (Join-Path $PSScriptRoot "common.ps1")
$ErrorActionPreference = "Continue"
function Ok($m)   { Write-Host "  ✅ $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  ⚠️  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  ❌ $m" -ForegroundColor Red; $Script:Failed = $true }
$Script:Failed = $false

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "❌ PowerShell را با Run as Administrator باز کنید." -ForegroundColor Red; exit 1 }

Write-Host "`n📁 پوشهٔ ربات: $AppDir`n" -ForegroundColor Cyan

# ─────────────── ۱. فایل‌ها و .env ───────────────
Write-Host "۱) بررسی فایل‌ها" -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $AppDir "main.py"))) { Fail "main.py در $AppDir نیست."; exit 1 }
if (Test-Path (Join-Path $AppDir "bot.py")) { Fail "این پوشهٔ ربات اصلی است! ربات ارزش منطقه‌ای باید پوشهٔ جدا داشته باشد."; exit 1 }
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $AppDir ".env.example") $EnvFile
    Warn "فایل .env نبود و از روی نمونه ساخته شد. BOT_TOKEN و ADMIN_ID را پر کنید و دوباره اجرا کنید:"
    Write-Host "     notepad $EnvFile"; exit 1
}
$token = Get-EnvValue "BOT_TOKEN"; $admin = Get-EnvValue "ADMIN_ID"
if (-not $token) { Fail "BOT_TOKEN در .env خالی است." } else { Ok "BOT_TOKEN تنظیم شده" }
if (-not $admin) { Warn "ADMIN_ID خالی است — پیام قطعی به کسی ارسال نمی‌شود." } else { Ok "ADMIN_ID = $admin" }
if (-not (Get-EnvValue "NESHAN_API_KEY")) { Warn "NESHAN_API_KEY خالی است — استعلام آدرس کار نمی‌کند." }

$db = Join-Path (Join-Path $AppDir "data") "bot.sqlite3"
$dbOverride = Get-EnvValue "DB_PATH"; if ($dbOverride) { $db = $dbOverride }
if (Test-Path $db) { Ok ("دیتابیس پیدا شد: {0} ({1:N1} MB)" -f $db, ((Get-Item $db).Length / 1MB)) }
else { Warn "دیتابیس $db پیدا نشد — ربات دیتابیس خالی می‌سازد. اگر قبلاً منتقل کرده‌اید مسیرش را چک کنید." }

# ─────────────── ۲. تداخل با ربات اصلی ───────────────
Write-Host "`n۲) بررسی تداخل با ربات‌های دیگر" -ForegroundColor Cyan
$envFiles = @(Get-ChildItem "C:\apps" -Filter ".env" -Recurse -Depth 2 -Force -File -ErrorAction SilentlyContinue)
$conflict = $false
foreach ($f in $envFiles) {
    if ($f.FullName -ieq $EnvFile) { continue }
    $other = Get-EnvValue "BOT_TOKEN" $f.FullName
    if (-not $other) { $other = Get-EnvValue "TELEGRAM_BOT_TOKEN" $f.FullName }
    if ($other -and $token -and $other -eq $token) {
        Fail "توکن این ربات با توکن ربات دیگر یکسان است: $($f.FullName) — یک توکن جدا از BotFather بله بگیرید."
        $conflict = $true
    }
}
if (-not $conflict) { Ok "توکن تکراری نیست ($($envFiles.Count - 1) ربات دیگر بررسی شد)" }

$mainBot = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "bot\.py" }
if ($mainBot) { Ok "ربات اصلی در حال اجراست (PID $($mainBot[0].ProcessId)) — به آن دست زده نمی‌شود" }
$manual = @(Get-BotProcesses)
if ($manual.Count -gt 0) { Warn "ربات ارزش منطقه‌ای همین حالا دستی اجراست (PID $($manual.ProcessId -join ', ')) — هنگام نصب بسته و تحت ناظر اجرا می‌شود." }
Ok "پورت: این ربات پورتی باز نمی‌کند (پنل ربات اصلی روی 3000 بی‌تأثیر است)"
if ($Script:Failed) { Write-Host "`nنصب متوقف شد؛ موارد قرمز را برطرف کنید." -ForegroundColor Red; exit 1 }

# ─────────────── ۳. پایتون و وابستگی‌ها ───────────────
Write-Host "`n۳) بررسی venv و پکیج‌ها" -ForegroundColor Cyan
$check = "import aiogram, aiohttp, requests, bs4, reportlab, arabic_reshaper, bidi, jdatetime, dotenv, openpyxl, pypdf, fitz, PIL, numpy"
$venvOk = $false
if (Test-Path $Python) {
    & $Python -c $check 2>$null
    $venvOk = ($LASTEXITCODE -eq 0)
}
if ($venvOk) { Ok "venv سالم است و همهٔ پکیج‌ها نصب‌اند" }
else {
    Warn "venv نیست یا ناقص است (venv کپی‌شده از کامپیوتر دیگر معمولاً روی سرور کار نمی‌کند). ساخت دوباره..."
    $basePy = $null
    foreach ($c in @("py -3", "python")) {
        try { $v = (& cmd /c "$c -c `"import sys;print(sys.executable)`"" 2>$null); if ($LASTEXITCODE -eq 0 -and $v) { $basePy = $v.Trim(); break } } catch {}
    }
    if (-not $basePy -or $basePy -like "*WindowsApps*") { Fail "Python روی سرور پیدا نشد. Python 3.11+ را با تیک «Add to PATH» و «Install for all users» نصب کنید."; exit 1 }
    if (Test-Path (Join-Path $AppDir "venv")) {
        $bak = Join-Path $AppDir ("venv_old_" + (Get-Date -Format "yyyyMMddHHmmss"))
        Rename-Item (Join-Path $AppDir "venv") $bak; Warn "venv قبلی به $bak تغییر نام داد"
    }
    & $basePy -m venv (Join-Path $AppDir "venv")
    & $Python -m pip install --upgrade pip -q
    & $Python -m pip install -r (Join-Path $AppDir "requirements.txt") -q
    & $Python -c $check
    if ($LASTEXITCODE -ne 0) { Fail "نصب پکیج‌ها ناموفق بود (اینترنت/دسترسی به PyPI را بررسی کنید)."; exit 1 }
    Ok "venv ساخته و پکیج‌ها نصب شد"
}
& $Python -m py_compile (Join-Path $AppDir "main.py")
if ($LASTEXITCODE -ne 0) { Fail "main.py خطای نحوی دارد."; exit 1 }

# ─────────────── ۴. تسک‌های Task Scheduler ───────────────
Write-Host "`n۴) ثبت اجرای خودکار" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match "watchdog\.ps1|supervisor\.ps1" -and $_.CommandLine -like "*$AppDir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($p in Get-BotProcesses)        { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }
Unregister-ScheduledTask -TaskName $TaskName  -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $WatchName -Confirm:$false -ErrorAction SilentlyContinue

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$psArgs = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File"

$supAction = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $AppDir `
    -Argument "$psArgs `"$(Join-Path $PSScriptRoot 'supervisor.ps1')`""
$supSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$bootTrigger.Delay = "PT30S"   # کمی صبر تا شبکه بالا بیاید
Register-ScheduledTask -TaskName $TaskName -Action $supAction -Trigger $bootTrigger -Principal $principal `
    -Settings $supSettings -Description "ربات بله ارزش منطقه‌ای — ناظر با ری‌استارت خودکار" -ErrorAction Stop | Out-Null
Ok "تسک $TaskName (اجرا هنگام روشن شدن سرور)"

$wdAction = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $AppDir `
    -Argument "$psArgs `"$(Join-Path $PSScriptRoot 'watchdog.ps1')`""
$wdTrigger = New-ScheduledTaskTrigger -AtStartup
$wdTrigger.Delay = "PT3M"
Register-ScheduledTask -TaskName $WatchName -Action $wdAction -Trigger $wdTrigger `
    -Principal $principal -Settings $supSettings -Description "پایش ۵ دقیقه‌ای ربات ارزش منطقه‌ای" -ErrorAction Stop | Out-Null
Ok "تسک $WatchName (پایش هر ۵ دقیقه)"

# دستور مدیریتی rvbot (از هر cmd/PowerShell)
$cmd = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'rvbot.ps1')`" %*`r`n"
[IO.File]::WriteAllText((Join-Path $env:SystemRoot "rvbot.cmd"), $cmd, [Text.Encoding]::ASCII)
Ok "دستور rvbot نصب شد"

# ─────────────── ۵. اجرا و آزمون ───────────────
Write-Host "`n۵) اجرای ربات" -ForegroundColor Cyan
Start-ScheduledTask -TaskName $TaskName
Start-ScheduledTask -TaskName $WatchName
Start-Sleep -Seconds 25
if (@(Get-BotProcesses).Count -gt 0) {
    Ok "ربات در حال اجراست"
    Write-Host "`n╔══════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host   "║   ✅ نصب کامل شد                         ║" -ForegroundColor Green
    Write-Host   "╚══════════════════════════════════════════╝" -ForegroundColor Green
    Write-Host "باید پیام «▶️ ربات ارزش منطقه‌ای اجرا شد» در بله به ادمین رسیده باشد."
} else {
    Fail "ربات بالا نیامد. آخرین لاگ:"
    $last = Get-ChildItem $LogDir -Filter "bot-*.err.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($last) { Get-Content $last.FullName -Tail 30 -Encoding UTF8 }
}
@"

دستورها (در cmd یا PowerShell با دسترسی Administrator):
  rvbot status    وضعیت
  rvbot logs      لاگ زنده
  rvbot restart   ری‌استارت
  rvbot stop      توقف (پایشگر تا rvbot start دخالت نمی‌کند)
  rvbot start     اجرا
  rvbot test      پیام آزمایشی به ادمین
  rvbot uninstall حذف اجرای خودکار (فایل‌ها و دیتابیس می‌مانند)
"@
