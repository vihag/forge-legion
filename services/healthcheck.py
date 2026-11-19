"""Probe loopback /healthz for a service port named by an environment variable."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m services.healthcheck PORT_ENV_NAME", file=sys.stderr)
        return 2
    env_name = args[0]
    port = os.environ.get(env_name)
    if port is None or port.strip() == "":
        print(f"{env_name} is not set", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{port.strip()}/healthz"
    try:
        with urllib.request.urlopen(url) as response:
            if response.getcode() != 200:
                return 1
    except urllib.error.URLError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())