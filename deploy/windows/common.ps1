# توابع مشترک ناظر/پایشگر/نصب ربات ارزش منطقه‌ای (ویندوز)
$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$Script:AppDir   = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Script:TaskName = "RegionalValueBot"
$Script:WatchName = "RegionalValueBot-Watchdog"
$Script:LogDir   = Join-Path $AppDir "logs"
$Script:StateDir = Join-Path $LogDir "state"
$Script:Python   = Join-Path $AppDir "venv\Scripts\python.exe"
$Script:EnvFile  = Join-Path $AppDir ".env"
New-Item -ItemType Directory -Force -Path $LogDir, $StateDir | Out-Null

function Get-EnvValue([string]$Key, [string]$File = $Script:EnvFile) {
    if (-not (Test-Path $File)) { return "" }
    $val = ""
    foreach ($line in [IO.File]::ReadAllLines($File, [Text.Encoding]::UTF8)) {
        if ($line -match "^\s*$Key\s*=\s*(.*)$") { $val = $Matches[1].Trim() }
    }
    if ($val.Length -ge 2 -and (($val[0] -eq '"' -and $val[-1] -eq '"') -or ($val[0] -eq "'" -and $val[-1] -eq "'"))) {
        $val = $val.Substring(1, $val.Length - 2)
    }
    return $val
}

function Write-SupLog([string]$Msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Msg
    Add-Content -Path (Join-Path $LogDir "supervisor.log") -Value $line -Encoding UTF8
}

function Get-TehranNow {
    try {
        $tz = [TimeZoneInfo]::FindSystemTimeZoneById("Iran Standard Time")
        return [TimeZoneInfo]::ConvertTime((Get-Date), $tz).ToString("yyyy-MM-dd HH:mm")
    } catch { return (Get-Date -Format "yyyy-MM-dd HH:mm") }
}

function Send-RawBale([string]$Text) {
    $token = Get-EnvValue "NOTIFY_BOT_TOKEN"; if (-not $token) { $token = Get-EnvValue "BOT_TOKEN" }
    $chat  = Get-EnvValue "NOTIFY_CHAT_ID";   if (-not $chat)  { $chat  = Get-EnvValue "ADMIN_ID" }
    $base  = Get-EnvValue "BALE_API_BASE";    if (-not $base)  { $base  = "https://tapi.bale.ai" }
    if (-not $token -or -not $chat) { Write-SupLog "notify: BOT_TOKEN/ADMIN_ID تنظیم نشده"; return $true }
    if ($Text.Length -gt 3800) { $Text = $Text.Substring(0, 3800) }
    $json  = @{ chat_id = $chat; text = $Text } | ConvertTo-Json -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    try {
        Invoke-RestMethod -Uri ("{0}/bot{1}/sendMessage" -f $base.TrimEnd('/'), $token) -Method Post `
            -ContentType "application/json; charset=utf-8" -Body $bytes -TimeoutSec 20 | Out-Null
        return $true
    } catch { Write-SupLog "notify failed: $($_.Exception.Message)"; return $false }
}

# ارسال پیام به ادمین؛ اگر اینترنت/بله قطع بود، پیام در صف می‌ماند و بعداً ارسال می‌شود
function Send-AdminAlert([string]$Text) {
    $full = "$Text`n🖥 $env:COMPUTERNAME | 🕒 $(Get-TehranNow)"
    $queue = Join-Path $StateDir "pending_alerts.txt"
    if (-not (Send-RawBale $full)) {
        Add-Content -Path $queue -Value ($full -replace "`r?`n", "\n") -Encoding UTF8
    }
}

function Send-PendingAlerts {
    $queue = Join-Path $StateDir "pending_alerts.txt"
    if (-not (Test-Path $queue)) { return }
    $lines = @(Get-Content $queue -Encoding UTF8)
    Remove-Item $queue -Force
    foreach ($l in $lines) {
        if (-not $l) { continue }
        $msg = "(ارسال با تأخیر) " + ($l -replace "\\n", "`n")
        if (-not (Send-RawBale $msg)) { Add-Content -Path $queue -Value $l -Encoding UTF8 }
    }
}

# پروسه‌های python main.py مربوط به همین پوشه (نه ربات اصلی)
function Get-BotProcesses {
    $dir = $Script:AppDir.ToLower()
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains("main.py") -and
                       ($_.CommandLine.ToLower().Contains($dir) -or
                        ((Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).Path -and
                         (Get-Process -Id $_.ProcessId).Path.ToLower().StartsWith($dir))) }
}

function Get-SupervisorProcesses {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains("supervisor.ps1") -and
                       $_.CommandLine.ToLower().Contains($Script:AppDir.ToLower()) }
}

function Get-LogTail([string]$File, [int]$N = 15) {
    if (-not (Test-Path $File)) { return "" }
    $t = (Get-Content $File -Tail $N -Encoding UTF8 -ErrorAction SilentlyContinue) -join "`n"
    if ($t.Length -gt 1800) { $t = $t.Substring($t.Length - 1800) }
    return $t
}
