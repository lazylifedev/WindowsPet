from __future__ import annotations

from .powershell_read_models import WindowsInspectionArea
import math


class ResultValidationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_result(value: object, request_area: WindowsInspectionArea, max_results: int) -> dict:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "operation", "items"}: raise ResultValidationError("invalid_top_level")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1: raise ResultValidationError("schema_version_mismatch")
    if not isinstance(value["operation"], str) or value["operation"] != request_area.value: raise ResultValidationError("operation_mismatch")
    if not isinstance(value["items"], list): raise ResultValidationError("items_not_array")
    if len(value["items"]) > max_results:
        raise ResultValidationError("too_many_items")
    for item in value["items"]:
        if not isinstance(item, dict): raise ResultValidationError("invalid_process_keys" if request_area is WindowsInspectionArea.PROCESSES else "invalid_top_level")
        if request_area is WindowsInspectionArea.PROCESSES:
            if set(item) != {"name", "pid", "cpuSeconds", "workingSetMb"}: raise ResultValidationError("invalid_process_keys")
            if not isinstance(item["name"], str) or not item["name"]: raise ResultValidationError("invalid_process_name")
            if type(item["pid"]) is not int or item["pid"] < 0: raise ResultValidationError("invalid_process_pid")
            if item["cpuSeconds"] is not None and (type(item["cpuSeconds"]) not in (int, float) or not math.isfinite(item["cpuSeconds"])): raise ResultValidationError("invalid_process_cpu")
            if type(item["workingSetMb"]) not in (int, float) or not math.isfinite(item["workingSetMb"]) or item["workingSetMb"] < 0: raise ResultValidationError("invalid_process_working_set")
        elif request_area is WindowsInspectionArea.SERVICES:
            if set(item) != {"name", "displayName", "state", "startMode"} or not all(isinstance(item[k], str) and item[k] for k in item): raise ValueError("invalid_service")
        elif request_area is WindowsInspectionArea.EVENT_LOGS:
            keys = {"logName", "eventId", "level", "provider", "timeCreated", "message"}
            text_keys = ("logName", "level", "provider", "timeCreated", "message")
            if (set(item) != keys or not all(isinstance(item[key], str) for key in text_keys)
                    or not item["logName"] or len(item["logName"]) > 256
                    or type(item["eventId"]) is not int or item["eventId"] < 0
                    or len(item["level"]) > 128 or len(item["provider"]) > 256
                    or len(item["timeCreated"]) > 64 or len(item["message"]) > 2048):
                raise ResultValidationError("invalid_event_log")
        elif request_area is WindowsInspectionArea.REGISTRY:
            keys = {"catalog", "path", "valueName", "value"}
            if (set(item) != keys or item["catalog"] not in {"app_paths", "installed_apps"}
                    or not all(isinstance(item[key], str) for key in keys)
                    or not item["path"] or len(item["path"]) > 1024
                    or len(item["valueName"]) > 256 or len(item["value"]) > 512):
                raise ResultValidationError("invalid_registry")
        else:
            if set(item) != {"interfaceAlias", "status", "ipv4Addresses", "defaultGateway"} or not isinstance(item["interfaceAlias"], str) or not item["interfaceAlias"] or not isinstance(item["status"], str) or not isinstance(item["ipv4Addresses"], list) or (item["defaultGateway"] is not None and not isinstance(item["defaultGateway"], str)): raise ValueError("invalid_network")
            for address in item["ipv4Addresses"]:
                if set(address) != {"address", "prefixLength"} or not isinstance(address["address"], str) or not address["address"] or type(address["prefixLength"]) is not int or not 0 <= address["prefixLength"] <= 32: raise ValueError("invalid_network")
    return value
