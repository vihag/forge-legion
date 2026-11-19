"""Static deploy config: env coverage, no hard-coded URLs, nginx routing."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "compose.yml"
NGINX = ROOT / "deploy" / "nginx" / "default.conf.template"
MAKEFILE = ROOT / "Makefile"
ENV_EXAMPLE = ROOT / ".env.example"

VAR_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-[^}]*)?\}")


def _vars_in_text(text: str) -> set[str]:
    return set(VAR_PATTERN.findall(text))


def _compose_and_makefile_vars() -> set[str]:
    compose_text = COMPOSE.read_text(encoding="utf-8")
    nginx_text = NGINX.read_text(encoding="utf-8")
    makefile_text = MAKEFILE.read_text(encoding="utf-8")
    return _vars_in_text(compose_text) | _vars_in_text(nginx_text) | _vars_in_text(makefile_text)


def test_every_compose_var_is_in_env_example() -> None:
    referenced = _compose_and_makefile_vars()
    env_lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    documented = {
        line.split("=", 1)[0]
        for line in env_lines
        if "=" in line and not line.startswith("#")
    }
    missing = referenced - documented
    assert not missing, f"missing from .env.example: {sorted(missing)}"


LITERAL_URL = re.compile(r"https?://")
IP_LITERAL = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
)


def _strip_interpolation(text: str) -> str:
    text = re.sub(r"https?://\$\{[A-Za-z0-9_]+(?::-[^}]*)?\}", "", text)
    return VAR_PATTERN.sub("", text)


def test_no_literal_urls_or_hosts_in_compose_or_nginx() -> None:
    allowed_ips = {"0.0.0.0", "127.0.0.1"}
    for path in (COMPOSE, NGINX):
        stripped = _strip_interpolation(path.read_text(encoding="utf-8"))
        assert not LITERAL_URL.search(stripped), path.name
        assert "localhost" not in stripped, path.name
        assert "ngrok-free.dev" not in stripped, path.name
        for match in IP_LITERAL.finditer(stripped):
            if match.group(0) not in allowed_ips:
                pytest.fail(f"unexpected IP literal {match.group(0)!r} in {path.name}")


def test_env_example_secrets_are_empty() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    secrets = {"XAI_API_KEY", "MCP_ADAPTER_TOKEN", "NGROK_AUTHTOKEN"}
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    for key in secrets:
        assert values.get(key) == "", key


def test_gitignore_ignores_env_local_not_example() -> None:
    subprocess.run(
        ["git", "check-ignore", "-q", ".env.local"],
        cwd=ROOT,
        check=True,
    )
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            ["git", "check-ignore", "-q", ".env.example"],
            cwd=ROOT,
            check=True,
        )


def test_nginx_routes_cover_all_prefixes() -> None:
    text = NGINX.read_text(encoding="utf-8")
    for needle in (
        "= /healthz",
        "location /case/",
        "location /splunk/",
        "location /soar/",
        "= /mcp",
        "location /mcp/",
        "= /mcp/healthz",
    ):
        assert needle in text


def test_mcp_location_passes_path_unchanged() -> None:
    text = NGINX.read_text(encoding="utf-8")
    mcp_blocks = [
        line for line in text.splitlines() if "proxy_pass http://${MCP_UPSTREAM}" in line
    ]
    assert mcp_blocks
    for line in mcp_blocks:
        assert line.strip() == "proxy_pass http://${MCP_UPSTREAM};" or "/healthz" in line


def test_up_public_and_compose_read_tokens_from_env_local() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "up-public:" in makefile
    up_public = makefile.split("up-public:", 1)[1].split("\n\n", 1)[0]
    assert "set -a && . ./.env.local && set +a" in up_public
    assert 'if [ -z "$$MCP_ADAPTER_TOKEN" ]' in up_public
    assert 'if [ -z "$$NGROK_AUTHTOKEN" ]' in up_public
    compose_text = COMPOSE.read_text(encoding="utf-8")
    assert "MCP_ADAPTER_TOKEN: ${MCP_ADAPTER_TOKEN?}" in compose_text
    assert "NGROK_AUTHTOKEN: ${NGROK_AUTHTOKEN?}" in compose_text
    assert "../${ENV_FILE:-.env.local}" in compose_text
    assert "ENV_FILE ?=" in makefile


def test_ngrok_service_only_in_ngrok_profile_and_uses_env_domain() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    ngrok_idx = text.index("  ngrok:")
    ngrok_block = text[ngrok_idx:]
    assert "profiles:" in ngrok_block.split("volumes:")[0]
    assert "ngrok" in ngrok_block
    assert "--url=${NGROK_DOMAIN}" in ngrok_block
    assert "ngrok-free.dev" not in text

def _up_public_recipe() -> str:
    text = MAKEFILE.read_text()
    match = re.search(r"^up-public:.*?\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert match, "up-public target not found"
    return match.group(1)


def test_up_public_guard_and_compose_share_one_shell() -> None:
    """The token guard and compose up must be one recipe line (one shell)."""
    recipe = _up_public_recipe()
    lines = recipe.rstrip("\n").split("\n")
    for line in lines[:-1]:
        assert line.rstrip().endswith("\\"), f"recipe line not chained: {line!r}"
    assert "--profile ngrok up" in lines[-1]


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_up_public_uses_env_local_token_over_empty_shell_value(tmp_path: Path) -> None:
    """`MCP_ADAPTER_TOKEN= make up-public` must hand compose the .env.local token."""
    (tmp_path / "Makefile").write_text(MAKEFILE.read_text())
    (tmp_path / ".env.local").write_text(
        "MCP_ADAPTER_TOKEN=from-env-local\nNGROK_AUTHTOKEN=ngrok-from-env-local\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "seen.txt"
    fake = bin_dir / "docker"
    fake.write_text(f'#!/bin/sh\nprintf "%s" "$MCP_ADAPTER_TOKEN" > "{seen}"\n')
    fake.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "MCP_ADAPTER_TOKEN": "",
        "NGROK_AUTHTOKEN": "",
    }
    result = subprocess.run(
        ["make", "-s", "up-public"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert seen.read_text() == "from-env-local"
