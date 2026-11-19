"""Add keys from .env.example that are missing in the target env file (no overwrites)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _parse_keys(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge missing keys from .env.example")
    parser.add_argument(
        "--env-file",
        default=os.environ.get("ENV_FILE", ".env.local"),
        help="Target env file (default: ENV_FILE or .env.local)",
    )
    args = parser.parse_args(argv)
    target = ROOT / args.env_file
    example = ROOT / ".env.example"
    if not example.is_file():
        print(".env.example is missing", file=sys.stderr)
        return 1
    if not target.is_file():
        target.write_text("", encoding="utf-8")
    existing_lines = target.read_text(encoding="utf-8").splitlines()
    existing_keys = {
        line.split("=", 1)[0]
        for line in existing_lines
        if "=" in line and not line.strip().startswith("#")
    }
    example_values = _parse_keys(example)
    added: list[str] = []
    for key, value in example_values.items():
        if key in existing_keys:
            continue
        existing_lines.append(f"{key}={value}")
        added.append(key)
    if added:
        target.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
        for key in added:
            print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())