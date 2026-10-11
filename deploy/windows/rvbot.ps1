# مدیریت ربات ارزش منطقه‌ای:  rvbot status|logs|restart|stop|start|test|uninstall
param([string]$Cmd = "status")
. (Join-Path $PSScriptRoot "common.ps1")
$manualFlag = Join-Path $StateDir "manual_stop"

function Stop-Bot {
    New-Item -ItemType File -Force -Path $manualFlag | Out-Null
    foreach ($p in Get-BotProcesses) { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }
    Start-Sleep -Seconds 3
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    foreach ($p in Get-SupervisorProcesses) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}
function Start-Bot {
    Remove-Item $manualFlag -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $TaskName
}

switch ($Cmd.ToLower()) {
    "status" {
        $bot = @(Get-BotProcesses); $sup = @(Get-SupervisorProcesses)
        if ($bot.Count) { Write-Host "✅ ربات در حال اجراست (PID $($bot.ProcessId -join ', '))" -ForegroundColor Green }
        else { Write-Host "❌ ربات اجرا نیست" -ForegroundColor Red }
        Write-Host ("ناظر: " + $(if ($sup.Count) { "فعال" } else { "غیرفعال" }))
        if (Test-Path $manualFlag) { Write-Host "⏹ در حالت توقف دستی (rvbot start برای اجرا)" -ForegroundColor Yellow }
        Get-ScheduledTask -TaskName $TaskName, $WatchName -ErrorAction SilentlyContinue | Format-Table TaskName, State -AutoSize
        Write-Host "آخرین رویدادهای ناظر:"; Get-Content (Join-Path $LogDir "supervisor.log") -Tail 8 -Encoding UTF8 -ErrorAction SilentlyContinue
    }
    "logs" {
        $last = Get-ChildItem $LogDir -Filter "bot-*.err.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($last) { Write-Host "📄 $($last.FullName)  (Ctrl+C برای خروج)"; Get-Content $last.FullName -Tail 60 -Wait -Encoding UTF8 }
        else { Write-Host "لاگی پیدا نشد." }
    }
    "stop"    { Stop-Bot;  Write-Host "⏹ متوقف شد. پایشگر تا 'rvbot start' دخالت نمی‌کند." }
    "start"   { Start-Bot; Start-Sleep 10; & $PSCommandPath status }
    "restart" { Stop-Bot; Start-Sleep 2; Start-Bot; Start-Sleep 10; & $PSCommandPath status }
    "test"    { if (Send-RawBale "🔔 پیام آزمایشی ربات ارزش منطقه‌ای از $env:COMPUTERNAME") { Write-Host "✅ ارسال شد؛ در بله بررسی کنید." } else { Write-Host "❌ ارسال نشد — supervisor.log را ببینید." } }
    "uninstall" {
        Stop-Bot
        Stop-ScheduledTask -TaskName $WatchName -ErrorAction SilentlyContinue
        Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match "watchdog\.ps1" -and $_.CommandLine -like "*$AppDir*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Unregister-ScheduledTask -TaskName $TaskName  -Confirm:$false -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $WatchName -Confirm:$false -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $env:SystemRoot "rvbot.cmd") -ErrorAction SilentlyContinue
        Write-Host "اجرای خودکار حذف شد. فایل‌ها و دیتابیس دست نخوردند."
    }
    default { Write-Host "rvbot status | logs | restart | stop | start | test | uninstall" }
}
