"""Dev bootstrap — create a user from the CLI (no invite required).

Usage::

    cd backend
    python -m scripts.create_user --email alice@example.org --name "Alice"

Prompts for the password (hidden). Production uses the invite flow in
``backend/app/routers/auth.py``; this script exists only so Phase-1
developers can sign in before invites are wired.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

# Ensure ``backend/`` is on sys.path so ``from app...`` works whether the
# script is invoked from the repo root or from ``backend/``.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.auth.password import hash_password  # noqa: E402
from app.crypto import index as crypto_index  # noqa: E402
from app.crypto import pii as crypto_pii  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models.user import User  # noqa: E402


async def _create_user(email: str, name: str, password: str) -> None:
    email_norm = email.strip().lower()
    async with session_scope() as session:
        # Check the blind index for collisions.
        from sqlalchemy import select

        idx = crypto_index.blind_index(email_norm)
        existing = await session.execute(select(User).where(User.email_index == idx))
        if existing.scalar_one_or_none() is not None:
            print(f"User with email {email!r} already exists.", file=sys.stderr)
            sys.exit(1)

        user = User(
            email_index=idx,
            email_encrypted=crypto_pii.encrypt_pii(email_norm),
            name_encrypted=crypto_pii.encrypt_pii(name),
            password_hash=hash_password(password),
            kek_salt=crypto_pii.random_bytes(16),
        )
        session.add(user)
        await session.commit()
        print(f"Created user {email_norm} (id={user.id})")


def main() -> None:
    p = argparse.ArgumentParser(description="Create a user for local dev.")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    args = p.parse_args()

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm:  ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_create_user(args.email, args.name, password))


if __name__ == "__main__":
    main()
