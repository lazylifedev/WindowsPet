from __future__ import annotations

import hashlib

from .powershell_read_models import PowerShellReadPlan, WindowsInspectionArea, WindowsInspectionRequest


_HEADER = '''$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$params = $env:WINDOWSPET_PS_PARAMETERS | ConvertFrom-Json
function Get-NullableDouble($value) { if ($null -eq $value) { return $null }; return [double]$value }
function Get-NullableGateway($value) { if ($null -eq $value) { return $null }; return $value.NextHop }
try {
'''
_FOOTER = '''} catch {
    [Console]::Error.WriteLine("windows_pet_powershell_error")
    exit 1
}
'''

def _wrap(operation: str, rows: str) -> str:
    return _HEADER + rows + f'''$result = [ordered]@{{schemaVersion=1;operation="{operation}";items=@($items)}}
$json = $result | ConvertTo-Json -Compress -Depth 6
$encoding = [System.Text.UTF8Encoding]::new($false)
$writer = [System.IO.StreamWriter]::new([Console]::OpenStandardOutput(), $encoding)
$writer.AutoFlush = $true
$writer.Write($json)
$writer.Dispose()
''' + _FOOTER

def build_read_plan(request: WindowsInspectionRequest) -> PowerShellReadPlan:
    if request.area is WindowsInspectionArea.PROCESSES:
        rows = '''$items = @(Get-Process | Where-Object { $null -eq $params.query -or $_.ProcessName.IndexOf([string]$params.query, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | Sort-Object ProcessName,Id | Select-Object -First $params.maxResults | ForEach-Object { [ordered]@{name=$_.ProcessName;pid=[int]$_.Id;cpuSeconds=(Get-NullableDouble $_.CPU);workingSetMb=[math]::Round($_.WorkingSet64 / 1MB, 2)} })
'''
        timeout = 10.0
    elif request.area is WindowsInspectionArea.SERVICES:
        rows = '''$items = @(Get-Service | Where-Object { $null -eq $params.query -or $_.Name.IndexOf([string]$params.query, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or $_.DisplayName.IndexOf([string]$params.query, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | Sort-Object Name | Select-Object -First $params.maxResults | ForEach-Object { [ordered]@{name=$_.Name;displayName=$_.DisplayName;state=$_.Status.ToString();startMode=$_.StartType.ToString()} })
'''
        timeout = 15.0
    elif request.area is WindowsInspectionArea.EVENT_LOGS:
        rows = '''$logName = if ($null -eq $params.query -or [string]::IsNullOrWhiteSpace([string]$params.query)) { "System" } else { [string]$params.query }
$items = @(Get-WinEvent -LogName $logName -MaxEvents $params.maxResults | ForEach-Object { [ordered]@{logName=$logName;eventId=[int]$_.Id;level=([string]$_.LevelDisplayName);provider=([string]$_.ProviderName);timeCreated=($(if ($null -eq $_.TimeCreated) { "" } else { $_.TimeCreated.ToUniversalTime().ToString("o") }));message=(([string]$_.Message).Substring(0, [math]::Min(2048, ([string]$_.Message).Length)))} })
'''
        timeout = 15.0
    elif request.area is WindowsInspectionArea.REGISTRY:
        rows = r'''$catalog = [ordered]@{
    app_paths = @("HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths", "HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths")
    installed_apps = @("HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall")
}
$catalogName = if ($null -eq $params.query) { "app_paths" } else { [string]$params.query }
if (-not $catalog.Contains($catalogName)) { throw "unsupported_registry_catalog" }
$items = @()
foreach ($root in $catalog[$catalogName]) {
    if ($items.Count -ge $params.maxResults) { break }
    if (Test-Path -LiteralPath $root) {
        foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction Stop)) {
            if ($items.Count -ge $params.maxResults) { break }
            $displayName = [string](Get-ItemPropertyValue -LiteralPath $key.PSPath -Name "DisplayName" -ErrorAction SilentlyContinue)
            $items += [ordered]@{catalog=$catalogName;path=$key.PSPath.Substring(($key.PSPath.IndexOf("::") + 2));valueName="DisplayName";value=$displayName.Substring(0, [math]::Min(512, $displayName.Length))}
        }
    }
}
'''
        timeout = 15.0
    elif request.area is WindowsInspectionArea.WINGET:
        rows = r'''$winget = @(Get-Command winget.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$rawLines = @(& $winget search --name $params.query --count $params.maxResults --source winget --accept-source-agreements --disable-interactivity --nowarn 2>$null)
if ($LASTEXITCODE -ne 0) { throw "winget_search_failed" }
$items = @()
$dataStarted = $false
foreach ($line in $rawLines) {
    if ([string]$line -match '^\s*-{3,}\s*$') { $dataStarted = $true; continue }
    if (-not $dataStarted -or $items.Count -ge $params.maxResults) { continue }
    $match = [regex]::Match([string]$line, '^\s*(.*?)\s+([A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9._-]+)\s+(\S+)(?:\s+(.*))?\s*$')
    if ($match.Success) {
        $name = $match.Groups[1].Value.Trim()
        $id = $match.Groups[2].Value.Trim()
        $version = $match.Groups[3].Value.Trim()
        $items += [ordered]@{name=$name.Substring(0, [math]::Min(256, $name.Length));id=$id.Substring(0, [math]::Min(256, $id.Length));version=$version.Substring(0, [math]::Min(128, $version.Length))}
    }
}
'''
        timeout = 30.0
    else:
        rows = '''$items = @(Get-NetIPConfiguration | Sort-Object InterfaceAlias | Select-Object -First $params.maxResults | ForEach-Object { [ordered]@{interfaceAlias=$_.InterfaceAlias;status=$_.NetAdapter.Status.ToString();ipv4Addresses=@($_.IPv4Address | ForEach-Object { [ordered]@{address=$_.IPAddress;prefixLength=[int]$_.PrefixLength} });defaultGateway=(Get-NullableGateway $_.IPv4DefaultGateway)} })
'''
        timeout = 15.0
    script = _wrap(request.area.value, rows)
    return PowerShellReadPlan(request.area.value, script, hashlib.sha256(script.encode("utf-8")).hexdigest(), timeout)
