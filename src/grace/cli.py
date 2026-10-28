"""Command line: run the demo, serve the dashboard, or check the configuration."""

from __future__ import annotations

import argparse
import sys

from grace import receipts
from grace.rail import RailError, make_rail


def preflight(rail: str = "auto") -> int:
    """Say what this process will actually do before it does it. Refuses a bad key, loudly."""
    print("grace preflight")
    print("-" * 40)
    try:
        chosen = make_rail(rail)
    except RailError as error:
        print(f"rail      ERROR {error}")
        return 1
    print(f"rail      {chosen.mode}")
    print(f"signing   {receipts.ALGORITHM}")
    if chosen.mode == "simulated":
        print("note      no PayPal credentials found; every money movement is simulated locally.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grace")
    sub = parser.add_subparsers(dest="command")

    demo = sub.add_parser("demo", help="run a compensation story")
    demo.add_argument("--story", default="partial")
    demo.add_argument("--rail", default="auto", choices=["auto", "sandbox", "simulated"])
    demo.add_argument("--all", action="store_true")

    serve = sub.add_parser("serve", help="serve the dashboard")
    serve.add_argument("--port", type=int, default=8020)

    check = sub.add_parser("preflight", help="report the rail and signing configuration")
    check.add_argument("--rail", default="auto", choices=["auto", "sandbox", "simulated"])

    args = parser.parse_args(argv)
    if args.command == "demo":
        from grace.demo import _main as demo_main

        return demo_main(
            ["--story", args.story, "--rail", args.rail] + (["--all"] if args.all else [])
        )
    if args.command == "preflight":
        return preflight(args.rail)
    if args.command == "serve":
        import uvicorn

        uvicorn.run("grace.api:app", host="0.0.0.0", port=args.port)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
