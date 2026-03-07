#!/usr/bin/env python3
"""Lightweight diagnostics for dependency installation/runtime issues."""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
from urllib.parse import urlparse


def module_status(name: str) -> str:
    return "installed" if importlib.util.find_spec(name) else "missing"


def can_resolve(hostname: str) -> bool:
    try:
        socket.gethostbyname(hostname)
        return True
    except OSError:
        return False


def parse_proxy() -> str | None:
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.getenv(key)
        if value:
            return value
    return None


def main() -> int:
    print("== Python runtime ==")
    print(sys.version)

    print("\n== Critical modules ==")
    for module in ("fastapi", "uvicorn", "jinja2", "python_multipart"):
        print(f"- {module}: {module_status(module)}")

    print("\n== Network/proxy ==")
    proxy = parse_proxy()
    if proxy:
        parsed = urlparse(proxy)
        proxy_host = parsed.hostname
        print(f"- proxy configured: {proxy}")
        if proxy_host:
            print(f"- proxy DNS resolvable: {can_resolve(proxy_host)}")
    else:
        print("- proxy configured: no")

    pypi_host = "pypi.org"
    print(f"- pypi.org DNS resolvable: {can_resolve(pypi_host)}")

    print("\n== Remediation hints ==")
    print("1) If dependencies are missing, install with: pip install -r requirements.txt")
    print("2) If installation fails with proxy 403, set a reachable package mirror (PIP_INDEX_URL).")
    print("3) In CI, prebuild dependencies into an image/venv artifact to avoid live installs.")
    print("4) Run unit tests after install: python -m unittest discover -s tests -v")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
