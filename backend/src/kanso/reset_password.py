"""Forget a password changed in kanso, so PASSWORD_HASH from .env is the login password again:

    docker compose exec app python -m kanso.reset_password
    docker compose restart app

The restart makes the running app read the password again. Every session is signed out.
"""

import asyncio

from kanso.db import prefs
from kanso.db.session import Session, engine


async def reset() -> bool:
    """Remove the stored password; returns whether there was one."""
    async with Session() as db:
        had = await prefs.get(db, prefs.PASSWORD_HASH) is not None
        await prefs.remove(db, prefs.PASSWORD_HASH)
        await db.commit()
    return had


async def _main() -> bool:
    try:
        return await reset()
    finally:
        await engine.dispose()


def main() -> None:
    if asyncio.run(_main()):
        print("Done: the password is PASSWORD_HASH from .env again. Now restart the app:")
        print("  docker compose restart app")
    else:
        print("The password was never changed in kanso: it is PASSWORD_HASH from .env.")


if __name__ == "__main__":
    main()
