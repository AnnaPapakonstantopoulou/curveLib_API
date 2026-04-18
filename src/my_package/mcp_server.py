"""
MCP (Model Context Protocol) server for the IRS Pricing API.

Exposes swap pricing, scenario analysis, and timeseries endpoints
as tools that LLMs can call directly via the MCP protocol.

Usage:
    python mcp_server.py

Requires the IRS Pricing API to be running at http://127.0.0.1:8000
"""

import json
import asyncio
import httpx
from datetime import date
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

API_BASE = "http://127.0.0.1:8000"

app = Server("irs-pricing-mcp")


#tool definitions are in the list_tools endpoint, and the handlers are in the call_tool endpoint
#this is because in MCP, tools and their handlers are decoupled
#here I define your tools in one place and then implement the logic to call them separately
#the app.call_tool() function will receive the tool name and arguments from the LLM
#and then we route it to the appropriate API endpoint based on the tool name

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_swap_info",
            description=(
                "Returns descriptive metadata about a vanilla interest rate swap "
                "without pricing it. Use this to understand what the instrument is "
                "before pricing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "maturity":      {"type": "number",  "description": "Maturity in years (e.g. 5 for a 5Y swap)"},
                    "fixedRate":     {"type": "number",  "description": "Fixed coupon rate as a decimal (e.g. 0.045 for 4.5%)"},
                    "notional":      {"type": "number",  "description": "Notional amount in currency units (default 1,000,000)"},
                    "payOrReceive":  {"type": "string",  "enum": ["pay", "receive"], "description": "Whether the user pays or receives the fixed leg"},
                    "fixedFreq":     {"type": "integer", "description": "Fixed leg payment frequency per year (default 2 = semi-annual)"},
                    "floatFreq":     {"type": "integer", "description": "Float leg reset frequency per year (default 4 = quarterly)"},
                },
                "required": ["maturity", "fixedRate", "payOrReceive"],
            },
        ),
        Tool(
            name="price_swap",
            description=(
                "Prices a vanilla fixed-vs-float interest rate swap on the base "
                "bootstrapped curve. Returns present value (PV), par rate, DV01, "
                "and a plain-English explanation suitable for passing directly to "
                "an end user."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "maturity":      {"type": "number",  "description": "Maturity in years"},
                    "fixedRate":     {"type": "number",  "description": "Fixed rate as a decimal"},
                    "notional":      {"type": "number",  "description": "Notional (default 1,000,000)"},
                    "payOrReceive":  {"type": "string",  "enum": ["pay", "receive"]},
                    "fixedFreq":     {"type": "integer", "description": "Fixed leg frequency (default 2)"},
                    "floatFreq":     {"type": "integer", "description": "Float leg frequency (default 4)"},
                },
                "required": ["maturity", "fixedRate", "payOrReceive"],
            },
        ),
        Tool(
            name="price_swap_bumped",
            description=(
                "Reprices a vanilla IRS under a parallel shift of the discount curve. "
                "Returns base PV, bumped PV, and the PV change. Useful for scenario "
                "analysis or validating DV01 (use bump_bps=1 for that)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "maturity":      {"type": "number",  "description": "Maturity in years"},
                    "fixedRate":     {"type": "number",  "description": "Fixed rate as a decimal"},
                    "notional":      {"type": "number",  "description": "Notional (default 1,000,000)"},
                    "payOrReceive":  {"type": "string",  "enum": ["pay", "receive"]},
                    "fixedFreq":     {"type": "integer", "description": "Fixed leg frequency (default 2)"},
                    "floatFreq":     {"type": "integer", "description": "Float leg frequency (default 4)"},
                    "bump_bps":      {"type": "number",  "description": "Parallel shift in basis points (default 1bp)"},
                },
                "required": ["maturity", "fixedRate", "payOrReceive"],
            },
        ),
        Tool(
            name="get_swap_timeseries",
            description=(
                "Returns a daily timeseries of par rates and PV for a swap between "
                "two dates, using simulated historical market data. Useful for "
                "charting or historical analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "maturity":      {"type": "number",  "description": "Maturity in years"},
                    "fixedRate":     {"type": "number",  "description": "Fixed rate as a decimal"},
                    "notional":      {"type": "number",  "description": "Notional (default 1,000,000)"},
                    "payOrReceive":  {"type": "string",  "enum": ["pay", "receive"]},
                    "fixedFreq":     {"type": "integer", "description": "Fixed leg frequency (default 2)"},
                    "floatFreq":     {"type": "integer", "description": "Float leg frequency (default 4)"},
                    "start_date":    {"type": "string",  "description": "Start date in YYYY-MM-DD format"},
                    "end_date":      {"type": "string",  "description": "End date in YYYY-MM-DD format"},
                },
                "required": ["maturity", "fixedRate", "payOrReceive", "start_date", "end_date"],
            },
        ),
    ]


#tool handlers implement the logic to call the appropriate API endpoint based on the tool name 
#and arguments received from the LLM

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:

    # Shared default values
    arguments.setdefault("notional", 1_000_000.0)
    arguments.setdefault("fixedFreq", 2)
    arguments.setdefault("floatFreq", 4)

    endpoint_map = {
        "get_swap_info":       "/instrument/info",
        "price_swap":          "/instrument/pricing",
        "price_swap_bumped":   "/instrument/pricing/bumped",
        "get_swap_timeseries": "/instrument/pricing/timeseries",
    }

    if name not in endpoint_map:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    url = f"{API_BASE}{endpoint_map[name]}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=arguments)
            response.raise_for_status()
            data = response.json()
    except httpx.ConnectError:
        return [TextContent(
            type="text",
            text=(
                "Could not connect to the IRS Pricing API. "
                "Make sure it is running at http://127.0.0.1:8000 "
                "(uvicorn src.my_package.main:app --reload)"
            ),
        )]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"API error {e.response.status_code}: {e.response.text}")]

    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


#entry point to start the MCP server

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
