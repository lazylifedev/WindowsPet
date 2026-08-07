from __future__ import annotations

from .powershell_read_models import WindowsInspectionArea


def validate_result(value: object, request_area: WindowsInspectionArea, max_results: int) -> dict:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "operation", "items"}:
        raise ValueError("invalid_schema")
    if value["schemaVersion"] != 1 or value["operation"] != request_area.value or not isinstance(value["items"], list):
        raise ValueError("invalid_schema")
    if len(value["items"]) > max_results:
        raise ValueError("too_many_items")
    for item in value["items"]:
        if not isinstance(item, dict): raise ValueError("invalid_item")
        if request_area is WindowsInspectionArea.PROCESSES:
            if set(item) != {"name", "pid", "cpuSeconds", "workingSetMb"} or not isinstance(item["name"], str) or not item["name"] or type(item["pid"]) is not int or item["pid"] < 0 or (item["cpuSeconds"] is not None and not isinstance(item["cpuSeconds"], (int, float))) or not isinstance(item["workingSetMb"], (int, float)) or item["workingSetMb"] < 0: raise ValueError("invalid_process")
        elif request_area is WindowsInspectionArea.SERVICES:
            if set(item) != {"name", "displayName", "state", "startMode"} or not all(isinstance(item[k], str) and item[k] for k in item): raise ValueError("invalid_service")
        else:
            if set(item) != {"interfaceAlias", "status", "ipv4Addresses", "defaultGateway"} or not isinstance(item["interfaceAlias"], str) or not item["interfaceAlias"] or not isinstance(item["status"], str) or not isinstance(item["ipv4Addresses"], list) or (item["defaultGateway"] is not None and not isinstance(item["defaultGateway"], str)): raise ValueError("invalid_network")
            for address in item["ipv4Addresses"]:
                if set(address) != {"address", "prefixLength"} or not isinstance(address["address"], str) or not address["address"] or type(address["prefixLength"]) is not int or not 0 <= address["prefixLength"] <= 32: raise ValueError("invalid_network")
    return value
