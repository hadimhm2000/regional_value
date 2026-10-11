# ═══════════════════════════════════════════════════════════════
# ناظر ربات ارزش منطقه‌ای: ربات را اجرا می‌کند، اگر قطع شد ۱۰ ثانیه بعد
# دوباره اجرا می‌کند و به ادمین در بله خبر می‌دهد.
# توسط Task Scheduler (تسک RegionalValueBot) هنگام روشن شدن سرور اجرا می‌شود.
# ═══════════════════════════════════════════════════════════════
. (Join-Path $PSScriptRoot "common.ps1")

# فقط یک ناظر در هر لحظه (دو نمونه = دو polling با یک توکن = خرابی هر دو)
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Global\RegionalValueBotSupervisor", [ref]$created)
if (-not $created) { Write-SupLog "ناظر دیگری در حال اجراست؛ خروج."; exit 0 }

Remove-Item (Join-Path $StateDir "manual_stop") -ErrorAction SilentlyContinue

# اگر نمونهٔ دستی/یتیم ربات باز مانده، اول بسته شود
foreach ($p in Get-BotProcesses) {
    Write-SupLog "بستن نمونهٔ قبلی ربات PID $($p.ProcessId)"
    & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null
}

$uptimeMin = ((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalMinutes
if ($uptimeMin -lt 10) {
    Send-AdminAlert "🔄 سرور ریبوت شد؛ ربات ارزش منطقه‌ای به‌صورت خودکار اجرا شد."
} else {
    Send-AdminAlert "▶️ ربات ارزش منطقه‌ای اجرا شد."
}

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$crashTimes = New-Object System.Collections.Generic.List[datetime]
$hadCrash = $false

while ($true) {
    Send-PendingAlerts
    # پاک‌سازی لاگ‌های قدیمی‌تر از ۱۴ روز
    Get-ChildItem $LogDir -Filter "bot-*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } | Remove-Item -Force -ErrorAction SilentlyContinue

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $outLog = Join-Path $LogDir "bot-$stamp.out.log"
    $errLog = Join-Path $LogDir "bot-$stamp.err.log"
    $started = Get-Date
    Write-SupLog "اجرای ربات..."
    try {
        $proc = Start-Process -FilePath $Python -ArgumentList "`"$(Join-Path $AppDir 'main.py')`"" `
            -WorkingDirectory $AppDir -NoNewWindow -PassThru `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        Set-Content -Path (Join-Path $StateDir "bot.pid") -Value $proc.Id
        $null = $proc.Handle   # برای دریافت ExitCode
        if ($hadCrash) {
            Start-Sleep -Seconds 20
            if (-not $proc.HasExited) {
                Send-AdminAlert "✅ ربات ارزش منطقه‌ای پس از قطعی دوباره راه‌اندازی شد."
                $hadCrash = $false
            }
        }
        $proc.WaitForExit()
        $code = $proc.ExitCode
    } catch {
        $code = "خطای اجرا: $($_.Exception.Message)"
        Add-Content -Path $errLog -Value $code -Encoding UTF8
    }

    if (Test-Path (Join-Path $StateDir "manual_stop")) {
        Write-SupLog "توقف دستی؛ ناظر خارج می‌شود."
        Send-AdminAlert "⏹ ربات ارزش منطقه‌ای به‌صورت دستی متوقف شد."
        break
    }

    $ranSec = [int]((Get-Date) - $started).TotalSeconds
    Write-SupLog "ربات قطع شد (کد $code، پس از $ranSec ثانیه)"
    $now = Get-Date
    $crashTimes.Add($now)
    $crashTimes.RemoveAll({ param($t) $t -lt $now.AddHours(-1) }) | Out-Null
    $n = $crashTimes.Count
    $hadCrash = $true
    if ($n -le 3 -or ($n % 10) -eq 0) {
        $tail = Get-LogTail $errLog 15
        Send-AdminAlert ("⚠️ ربات ارزش منطقه‌ای قطع شد!`nکد خروج: $code (پس از $ranSec ثانیه اجرا)`n" +
                         "قطعی در یک ساعت اخیر: $n`n⏳ ۱۰ ثانیه دیگر خودکار راه‌اندازی می‌شود.`n`nآخرین لاگ‌ها:`n$tail")
    }
    # کرش‌های خیلی پشت‌سرهم ← فاصلهٔ بیشتر تا سرور درگیر نشود
    if ($n -gt 10) { Start-Sleep -Seconds 60 } else { Start-Sleep -Seconds 10 }
}
$mutex.ReleaseMutex()
