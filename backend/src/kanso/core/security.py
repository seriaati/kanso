import base64
import hashlib
import hmac
import time
from functools import cache
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Cookie, HTTPException, Response

from kanso.core.settings import settings
from kanso.db import prefs
from kanso.db.session import Session

SESSION_COOKIE = "kanso_session"
SESSION_MAX_AGE = 30 * 24 * 3600


def _derive_key(purpose: bytes) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose)
    return hkdf.derive(settings.secret_key.encode())


# Cached in memory (the app is a single process); reset_password needs an app restart to be seen.
_password_hash: str | None = None


async def password_hash() -> str:
    global _password_hash
    if _password_hash is None:
        async with Session() as db:
            _password_hash = await prefs.get(db, prefs.PASSWORD_HASH) or settings.password_hash
    return _password_hash


def remember_password_hash(value: str | None) -> None:
    global _password_hash
    _password_hash = value


# "Log out everywhere" bumps it, which signs every session out.
_session_epoch: int | None = None


async def session_epoch() -> int:
    global _session_epoch
    if _session_epoch is None:
        async with Session() as db:
            _session_epoch = int(await prefs.get(db, prefs.SESSION_EPOCH, 0))
    return _session_epoch


def remember_session_epoch(value: int | None) -> None:
    global _session_epoch
    _session_epoch = value


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PasswordHasher().verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Signing the password hash and epoch means changing either signs every session out.
# Epoch 0 signs the same message as cookies made before epochs existed.
def _sign(expires: int, password_hash: str, epoch: int) -> str:
    message = f"{expires}.{password_hash}" + (f".{epoch}" if epoch else "")
    return hmac.new(_derive_key(b"kanso session"), message.encode(), hashlib.sha256).hexdigest()


def set_session_cookie(response: Response, password_hash: str, epoch: int) -> None:
    expires = int(time.time()) + SESSION_MAX_AGE
    response.set_cookie(
        SESSION_COOKIE,
        f"{expires}.{_sign(expires, password_hash, epoch)}",
        max_age=SESSION_MAX_AGE,
        httponly=True,
        # Lax, not Strict: the cookie must come along when Google redirects back after OAuth.
        samesite="lax",
        secure=settings.secure_cookies,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, samesite="lax", secure=settings.secure_cookies
    )


def valid_session(cookie: str, password_hash: str, epoch: int) -> bool:
    expires, _, signature = cookie.partition(".")
    if not expires.isascii() or not expires.isdigit() or int(expires) < time.time():
        return False
    expected = _sign(int(expires), password_hash, epoch)
    # Bytes: compare_digest rejects non-ASCII str with a TypeError (a 500, not a 401).
    return hmac.compare_digest(signature.encode(), expected.encode())


async def require_session(
    kanso_session: Annotated[str | None, Cookie(include_in_schema=False)] = None,
) -> None:
    if not kanso_session or not valid_session(
        kanso_session, await password_hash(), await session_epoch()
    ):
        raise HTTPException(status_code=401, detail="Not logged in")


@cache
def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive_key(b"kanso token encryption")))


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode()
