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
        rows = '''$items = @(Get-CimInstance Win32_Service | Where-Object { $null -eq $params.query -or $_.Name.IndexOf([string]$params.query, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or $_.DisplayName.IndexOf([string]$params.query, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | Sort-Object Name | Select-Object -First $params.maxResults | ForEach-Object { [ordered]@{name=$_.Name;displayName=$_.DisplayName;state=$_.State;startMode=$_.StartMode} })
'''
        timeout = 15.0
    else:
        rows = '''$items = @(Get-NetIPConfiguration | Sort-Object InterfaceAlias | Select-Object -First $params.maxResults | ForEach-Object { [ordered]@{interfaceAlias=$_.InterfaceAlias;status=$_.NetAdapter.Status.ToString();ipv4Addresses=@($_.IPv4Address | ForEach-Object { [ordered]@{address=$_.IPAddress;prefixLength=[int]$_.PrefixLength} });defaultGateway=(Get-NullableGateway $_.IPv4DefaultGateway)} })
'''
        timeout = 15.0
    script = _wrap(request.area.value, rows)
    return PowerShellReadPlan(request.area.value, script, hashlib.sha256(script.encode("utf-8")).hexdigest(), timeout)
