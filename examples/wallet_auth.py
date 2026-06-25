"""Wallet-signed auth: EIP-191 session login + EIP-712 agent registration.

The wallet private key is supplied by the caller — this is a library pattern, so
there is no key prompt or file handling here. Replace the placeholders below
with a real key, agent address, and your environment's chain id.

Run against a gateway that verifies these flows (e.g. a local devnet)::

    python examples/wallet_auth.py
"""

from __future__ import annotations

import time

from nexus_exchange import Client, EthSigner, Network

# Canonical Hardhat/ethers account #0 — for illustration only. Never hardcode a
# real key; load it from your own secret store.
WALLET_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
AGENT_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"
CHAIN_ID = 393


def main() -> None:
    signer = EthSigner.from_hex(WALLET_PRIVATE_KEY)
    print("wallet:", signer.address)

    with Client(Network.LOCAL) as client:
        # EIP-191 personal_sign → POST /auth/login.
        session = client.sign_in(signer)
        print("logged in as", session.address)
        print("session token (secret):", session.token)

        # EIP-712 → POST /agents/register. Expiry must be in [now+1d, now+90d];
        # 30 days here. A monotonic millisecond timestamp is a safe nonce.
        now_ms = int(time.time() * 1000)
        registration = signer.register_agent(
            agent=AGENT_ADDRESS,
            expires_at_ms=now_ms + 30 * 24 * 60 * 60 * 1000,
            nonce=now_ms,
            chain_id=CHAIN_ID,
            label="example-bot",
        )
        registered = client.register_agent(registration)
        print("registered agent", registered.agent_address, "expires", registered.expires_at)


if __name__ == "__main__":
    main()
