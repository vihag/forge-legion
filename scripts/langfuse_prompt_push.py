#!/usr/bin/env python3
"""Push services/triage/prompt.py SYSTEM_PROMPT to Langfuse prompt management."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.triage.prompt import SYSTEM_PROMPT  # noqa: E402

PROMPT_NAME = "triage-system"
PRODUCTION_LABEL = "production"
REQUIRED_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _missing_keys() -> list[str]:
    return [key for key in REQUIRED_KEYS if not _env(key)]


def main() -> int:
    missing = _missing_keys()
    if missing:
        for key in missing:
            print(f"{key}: empty", file=sys.stderr)
        return 1
    import langfuse  # noqa: PLC0415

    client = langfuse.Langfuse(
        public_key=_env("LANGFUSE_PUBLIC_KEY"),
        secret_key=_env("LANGFUSE_SECRET_KEY"),
        base_url=_env("LANGFUSE_HOST"),
        timeout=5,
    )
    try:
        current = client.get_prompt(
            PROMPT_NAME,
            label=PRODUCTION_LABEL,
            type="text",
            cache_ttl_seconds=0,
        )
        if (
            isinstance(current.prompt, str)
            and current.prompt == SYSTEM_PROMPT
            and not getattr(current, "is_fallback", False)
        ):
            print(f"unchanged v{current.version}")
            return 0
    except Exception:
        current = None
    created = client.create_prompt(
        name=PROMPT_NAME,
        prompt=SYSTEM_PROMPT,
        labels=[PRODUCTION_LABEL],
        type="text",
        commit_message="seeded from services/triage/prompt.py",
    )
    version = getattr(created, "version", None)
    if version is None and current is not None:
        version = getattr(current, "version", "?")
    print(f"pushed v{version}")
    try:
        client.shutdown()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())