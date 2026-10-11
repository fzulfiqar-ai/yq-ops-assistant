"""python -m scripts.ops <command> [...]   (the one entry point of the ops engine)

Each command prints a ready text block (what the skill relays, verbatim) or, with --json, one JSON
object: {ok, command, data_date, text, flags, files, warnings}. Exit code: 0 fine, 2 needs
attention or is blocked, 1 error. Output is forced to UTF-8 here, so the command text never needs
an environment prefix and one permission rule matches it.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback

# command -> module (each module has add_arguments(parser) and run(args) -> dict)
COMMANDS: dict[str, tuple[str, str]] = {
    "status": ("scripts.ops.status", "where things stand: data dates, folder vs live site, lock, what is waiting"),
    "selftest": ("scripts.ops.selftest", "the engine's own checks (--repo-clean before a commit, --live for the read-only proof)"),
    "load": ("scripts.ops.load_check", "check a Focus drop folder (read-only) / follow a running load"),
    "brief": ("scripts.ops.sales", "sales brief: latest day, 3 days, 7 days, month, what to do today"),
    "watch": ("scripts.ops.checks", "the action list: things to check"),
    "moves": ("scripts.ops.moves", "storekeeper and van movement audit"),
    "stock": ("scripts.ops.stock", "stock cover, order list, fast movers, ageing"),
    "report": ("scripts.ops.workbook.build", "weekly and monthly workbooks"),
    "recon": ("scripts.ops.recon", "month-end reconciliations and count sheets"),
    "kpi": ("scripts.ops.kpi", "KPI scorecard and the evidence log"),
    "check": ("scripts.ops.textcheck", "check the numbers in a drafted text against computed facts"),
}
NO_BANNER = {"load", "selftest"}        # they report the lock themselves


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        for name, (_, about) in COMMANDS.items():
            print(f"  {name:9} {about}")
        return 0
    name = argv[0]
    if name not in COMMANDS:
        print(f"unknown command '{name}'. Commands: {', '.join(COMMANDS)}")
        return 1
    try:
        module = importlib.import_module(COMMANDS[name][0])
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("scripts.ops"):
            print(f"'{name}' is not built yet.")
            return 1
        raise

    parser = argparse.ArgumentParser(prog=f"python -m scripts.ops {name}", description=COMMANDS[name][1])
    parser.add_argument("--data-root", help="the project folder that holds Uplaods/, business_data/ and exports/")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of the text block")
    module.add_arguments(parser)
    # intermixed: words and options may come in any order (`brief mtd --json rep:<name>`)
    args = parser.parse_intermixed_args(argv[1:])

    from scripts.ops import config, paths
    if args.data_root:
        paths.set_data_root(args.data_root)

    banner = None
    if name not in NO_BANNER:
        from scripts.ops import status
        try:
            banner = status.interrupted_banner()
        except OSError as exc:              # the lock could not be read: say so, never hide it
            banner = f"The load lock could not be read ({type(exc).__name__}): run python -m scripts.ops status"

    try:
        result = module.run(args)
    except config.ConfigMissing as exc:
        print(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR in '{name}': {type(exc).__name__}: {str(exc)[:300]}")
        traceback.print_exc(file=sys.stderr)
        return 1

    if args.json:
        if banner:
            result = dict(result, banner=banner)
        print(json.dumps(result, ensure_ascii=True, default=str))
    else:
        if banner:
            print(banner)
            print()
        print(result.get("text") or "")
        for w in result.get("warnings") or []:
            print(f"(warning: {w})")
    if "exit" in result:
        return int(result["exit"])
    return 0 if result.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
