"""One-time runtime contract audit; records field names without response values."""

import asyncio
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from fastmcp import Client


URL = "http://127.0.0.1:8002/mcp"
OUT = Path("docs/tool_contract_runtime.json")
START = date.today()
CHECK_IN = START + timedelta(days=30)
CHECK_OUT = CHECK_IN + timedelta(days=1)

CASES = [
    ("find_places", {"destination": "Hà Nội", "limit": 1}),
    ("find_nearby", {"query": "cà phê", "lat": 21.03, "lng": 105.85, "limit": 1, "with_coordinates": False}),
    ("search_places", {"query": "quán cà phê Hà Nội", "limit": 1}),
    ("find_stays", {"destination": "Hà Nội", "limit": 1}),
    ("search_hotels", {"destination": "Hà Nội", "check_in_date": CHECK_IN.isoformat(), "check_out_date": CHECK_OUT.isoformat(), "limit": 1}),
    ("find_festivals", {"location": "Hà Nội", "start_date": START.isoformat(), "limit": 1}),
    ("search_events", {"location": "Hà Nội", "start_date": START.isoformat(), "max_results": 1}),
    ("get_place_details", {"place_id": "Hồ Hoàn Kiếm", "source": "db"}),
    ("get_place_photos", {"places": ["Hồ Hoàn Kiếm"], "destination": "Hà Nội", "source": "db"}),
    ("get_place_reviews", {"place_id": "Hồ Hoàn Kiếm", "source": "db"}),
]


def shape(value, depth=3):
    if depth <= 0:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth - 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth - 1)] if value else []
    return type(value).__name__


async def main():
    selected = set(sys.argv[1:])
    audit = json.loads(OUT.read_text(encoding="utf-8")) if selected and OUT.exists() else {"endpoint": URL, "date": START.isoformat(), "tools": {}, "calls": {}}
    audit["date"] = START.isoformat()
    local_schema = "--local-schema" in selected
    if local_schema:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from src.fastmcp_server import mcp
        target = mcp
    else:
        target = URL
    async with Client(target) as client:
        tools = await client.list_tools()
        audit["tools"] = {}
        for tool in tools:
            schema = tool.input_schema or {}
            audit["tools"][tool.name] = {
                "required": schema.get("required", []),
                "inputs": sorted((schema.get("properties") or {}).keys()),
                "output_schema_keys": sorted((tool.output_schema or {}).keys()) if getattr(tool, "output_schema", None) else [],
            }
        print(f"listed {len(tools)} MCP tools", flush=True)
        if local_schema:
            OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
            return

        for name, args in CASES:
            if selected and name not in selected:
                continue
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(client.call_tool(name, args, raise_on_error=False), timeout=55)
                data = result.structured_content or {}
                audit["calls"][name] = {
                    "input": args,
                    "elapsed_s": round(time.monotonic() - started, 1),
                    "is_error": result.is_error,
                    "status": data.get("status") if isinstance(data, dict) else None,
                    "data_source": data.get("data_source") if isinstance(data, dict) else None,
                    "output_shape": shape(data),
                    "error": data.get("error") if isinstance(data, dict) else None,
                }
            except Exception as exc:
                audit["calls"][name] = {"input": args, "elapsed_s": round(time.monotonic() - started, 1), "exception": type(exc).__name__}
            print(name, audit["calls"][name].get("status", audit["calls"][name].get("exception")), audit["calls"][name]["elapsed_s"], flush=True)
            OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


asyncio.run(main())
