"""Check the public gateway's health — no credentials.

python examples/health_check.py
"""

from __future__ import annotations

from nexus_exchange import Client


def main() -> None:
    with Client() as client:
        health = client.health_check()
        print("gateway health:")
        print(health)


if __name__ == "__main__":
    main()
