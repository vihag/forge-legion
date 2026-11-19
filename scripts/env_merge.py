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


def _refuse_secret_key(key: str) -> bool:
    return key.endswith("_TOKEN") or key.endswith("_KEY")


def _set_key(target: Path, key: str, value: str) -> int:
    if _refuse_secret_key(key):
        print(f"refused {key}", file=sys.stderr)
        return 1
    if not target.is_file():
        target.write_text("", encoding="utf-8")
    lines = target.read_text(encoding="utf-8").splitlines()
    updated = False
    output: list[str] = []
    for line in lines:
        if line.startswith("#") or "=" not in line:
            output.append(line)
            continue
        line_key = line.split("=", 1)[0]
        if line_key == key:
            output.append(f"{key}={value}")
            updated = True
        else:
            output.append(line)
    if not updated:
        output.append(f"{key}={value}")
    target.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(key)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge missing keys from .env.example")
    parser.add_argument(
        "--env-file",
        default=os.environ.get("ENV_FILE", ".env.local"),
        help="Target env file (default: ENV_FILE or .env.local)",
    )
    parser.add_argument("--set", dest="set_key", help="Set or replace one key in the env file")
    parser.add_argument("--value", dest="set_value", default="", help="Value for --set")
    args = parser.parse_args(argv)
    target = ROOT / args.env_file
    if args.set_key is not None:
        return _set_key(target, args.set_key, args.set_value)
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