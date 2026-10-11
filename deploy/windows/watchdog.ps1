# پایشگر (حلقهٔ دائمی، هر ۵ دقیقه): اگر ناظر یا ربات اجرا نیست (و توقف دستی نبوده) دوباره اجرا می‌کند.
. (Join-Path $PSScriptRoot "common.ps1")

function Invoke-Check {
Send-PendingAlerts
if (Test-Path (Join-Path $StateDir "manual_stop")) { return }

$sup = @(Get-SupervisorProcesses)
$bot = @(Get-BotProcesses)
if ($sup.Count -gt 0 -and $bot.Count -gt 0) { return }
if ($sup.Count -gt 0 -and $bot.Count -eq 0) { return }   # ناظر در فاصلهٔ ری‌استارت است

# ناظر اجرا نیست ← ربات یتیم را ببند و تسک اصلی را اجرا کن
foreach ($p in $bot) { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }
Write-SupLog "پایشگر: ناظر اجرا نبود؛ اجرای دوباره."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 40
if (@(Get-BotProcesses).Count -gt 0) {
    Send-AdminAlert "🩺 پایشگر: ربات ارزش منطقه‌ای اجرا نبود و دوباره راه‌اندازی شد."
} else {
    $last = Get-ChildItem $LogDir -Filter "bot-*.err.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $tail = if ($last) { Get-LogTail $last.FullName 15 } else { "" }
    Send-AdminAlert "🚨 پایشگر: ربات ارزش منطقه‌ای بالا نمی‌آید! بررسی دستی لازم است.`n`n$tail"
}
}

while ($true) {
    try { Invoke-Check } catch { Write-SupLog "پایشگر خطا: $($_.Exception.Message)" }
    Start-Sleep -Seconds 300
}
