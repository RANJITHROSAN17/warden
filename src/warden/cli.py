"""Command line entry points."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="warden", description="Spend authority for AI agents.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("demo", help="run the five offline scenarios and verify the receipts")

    serve = sub.add_parser("serve", help="start the ledger UI and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    verify = sub.add_parser("verify", help="verify a receipt printed by the demo or the API")
    verify.add_argument("receipt", help="path to a JSON file containing a signed receipt")

    args = parser.parse_args(argv)

    if args.command == "demo" or args.command is None:
        from warden.demo import run

        return run()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("warden.api:app", host=args.host, port=args.port, reload=False)
        return 0

    if args.command == "verify":
        import json
        from pathlib import Path

        from warden import receipts

        payload = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
        ok, message = receipts.verify(payload)
        print(f"{'VALID  ' if ok else 'INVALID'}  {message}")
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
