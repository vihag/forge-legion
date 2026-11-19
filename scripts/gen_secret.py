"""Write a random 64-hex secret into an env file for one key when the value is empty."""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a secret for one env key")
    parser.add_argument("KEY", help="Environment variable name to set")
    parser.add_argument(
        "--env-file",
        default=os.environ.get("ENV_FILE", ".env.local"),
        help="Target env file (default: ENV_FILE or .env.local)",
    )
    args = parser.parse_args(argv)
    target = ROOT / args.env_file
    if not target.is_file():
        print(f"{args.env_file} is missing; run make env-merge first", file=sys.stderr)
        return 1
    lines = target.read_text(encoding="utf-8").splitlines()
    prefix = f"{args.KEY}="
    found = False
    updated: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            found = True
            current = line.split("=", 1)[1] if "=" in line else ""
            if current.strip():
                print(f"{args.KEY} already set", file=sys.stderr)
                return 1
            updated.append(f"{prefix}{secrets.token_hex(32)}")
        else:
            updated.append(line)
    if not found:
        updated.append(f"{prefix}{secrets.token_hex(32)}")
    target.write_text("\n".join(updated) + "\n", encoding="utf-8")
    print(f"{args.KEY} set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())