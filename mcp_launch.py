"""Launcher for the YQ Ops MCP server under Claude Desktop.

Claude Desktop does not reliably honour the `cwd` field when the path contains
spaces, so `python -m app.mcp_server` fails with ModuleNotFoundError. This wrapper
hard-pins the project root on sys.path + chdir and loads .env by absolute path, then
runs the server module — so it works regardless of the launch directory.
"""
import os
import runpy
import sys

PROJECT = r"C:\Users\fahmed\OneDrive - YqBahrain\Desktop\YQ Bahrain Mobile Accessories"
os.chdir(PROJECT)
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT, ".env"))
except Exception:
    pass
runpy.run_module("app.mcp_server", run_name="__main__")
