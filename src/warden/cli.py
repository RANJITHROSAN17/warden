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

    sub.add_parser(
        "preflight", help="check the PayPal rail and the intent judge before a demo or a run"
    )

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

    if args.command == "preflight":
        return _preflight()

    if args.command == "verify":
        import json
        from pathlib import Path

        from warden import receipts

        payload = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
        ok, message = receipts.verify(payload)
        print(f"{'VALID  ' if ok else 'INVALID'}  {message}")
        return 0 if ok else 1

    return 1


def _preflight() -> int:
    """Report the rail, the judge and the signing key - the three things that make a run honest.

    A wrong client id and a wrong endpoint both present as the same confusing error mid-demo, so
    this answers both before anything is filmed.
    """
    import os

    print("warden preflight")
    print("-" * 60)

    mode = os.environ.get("WARDEN_PAYPAL_MODE", "auto")
    client = bool(os.environ.get("WARDEN_PAYPAL_CLIENT_ID"))
    secret = bool(os.environ.get("WARDEN_PAYPAL_CLIENT_SECRET"))
    print(f"paypal mode      {mode}")
    print(f"client id set    {'yes' if client else 'no'}")
    print(f"client secret    {'yes' if secret else 'no'}")

    if client and secret:
        from warden.paypal import PayPalError, SandboxPayPal

        try:
            token = SandboxPayPal()._token()
            print(f"oauth token      obtained ({token[:6]}...)")
            print("rail             sandbox - real calls will be made")
        except PayPalError as exc:
            print(f"oauth token      FAILED: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001 - preflight reports, it does not raise
            print(f"oauth token      FAILED: {exc}")
            return 1
    else:
        print("rail             simulated - every receipt will say 'simulated'")

    judge = "model" if os.environ.get("WARDEN_LLM_API_KEY") else "lexical-overlap"
    print(f"intent judge     {judge}")
    key = os.environ.get("WARDEN_SIGNING_KEY", "")
    print(f"signing key      {'set' if key else 'default dev key (fine for a demo, not evidence)'}")
    print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
