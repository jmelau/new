"""Shared Garmin Connect authentication.

Auth strategy, in priority order:

1. A sealed token file (data/garmin_token.enc) unlocked with TOKEN_KEY.
2. The GARMIN_TOKENS secret (bootstrap, used on the very first run).
3. EMAIL + PASSWORD with an interactive MFA prompt — local use only.

Password login from a datacenter IP is what gets accounts rate-limited (HTTP 429)
and temporarily blocked, so CI never takes path 3. Once authenticated, the caller
must persist the token immediately: Garmin rotates the refresh token on every use.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from garminconnect import Garmin

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = REPO_ROOT / "data" / "garmin_token.enc"


def _token_from_env_or_vault() -> tuple[str | None, str]:
    """Return (token_json, source_label)."""
    passphrase = os.getenv("TOKEN_KEY", "")
    if passphrase:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from vault import unseal

        sealed = unseal(passphrase, TOKEN_FILE)
        if sealed:
            return sealed, "sealed token file"

    bootstrap = os.getenv("GARMIN_TOKENS", "").strip()
    if bootstrap:
        return bootstrap, "GARMIN_TOKENS secret"

    return None, "none"


def connect(*, allow_password: bool = False, verbose: bool = True) -> Garmin:
    """Authenticate and return a ready Garmin client."""
    token_json, source = _token_from_env_or_vault()

    if token_json:
        # login() treats a >512 char tokenstore as literal token data, but writing
        # it to a temp file is unambiguous and lets the library manage its own state.
        tmpdir = Path(tempfile.mkdtemp(prefix="garmintok-"))
        (tmpdir / "garmin_tokens.json").write_text(token_json)
        client = Garmin()
        client.login(tokenstore=str(tmpdir))
        if verbose:
            print(f"Authenticated as {client.full_name or client.display_name} "
                  f"(via {source})")
        return client

    if not allow_password:
        raise SystemExit(
            "No Garmin token available. Set TOKEN_KEY (with data/garmin_token.enc) "
            "or GARMIN_TOKENS. Run scripts/login_local.py on your own machine to "
            "mint one — never log in with a password from CI."
        )

    email = os.getenv("EMAIL") or input("Garmin Connect email: ").strip()
    password = os.getenv("PASSWORD")
    if not password:
        import getpass

        password = getpass.getpass("Garmin Connect password: ")

    client = Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    client.login()
    if verbose:
        print(f"Authenticated as {client.full_name or client.display_name} "
              f"(fresh password login)")
    return client


def dump_token(client: Garmin) -> str:
    """Serialise the client's current session tokens to JSON."""
    return client.client.dumps()


def persist_token(client: Garmin) -> bool:
    """Seal the (possibly rotated) token back to disk. Returns True if written."""
    passphrase = os.getenv("TOKEN_KEY", "")
    if not passphrase:
        return False
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vault import seal

    seal(dump_token(client), passphrase, TOKEN_FILE)
    return True


def summarise_token(token_json: str) -> str:
    data = json.loads(token_json)
    have = [k for k, v in data.items() if v]
    return f"token fields present: {', '.join(have)}"
