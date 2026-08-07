from __future__ import annotations
import json
from dataclasses import dataclass

@dataclass(frozen=True)
class ServiceRestartRequest:
    service_query: str
    snapshot: tuple[dict, ...] = ()

def parse_service_restart_request(arguments):
    data=json.loads(arguments) if isinstance(arguments,str) else arguments
    if not isinstance(data,dict) or set(data)!={"service_query"} or not isinstance(data["service_query"],str) or not data["service_query"].strip() or len(data["service_query"])>260 or any(ord(c)<32 for c in data["service_query"]):
        raise ValueError("request_service_restart_invalid_arguments")
    return ServiceRestartRequest(data["service_query"].strip())
