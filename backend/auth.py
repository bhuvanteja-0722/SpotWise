"""Local SpotWise account and cookie-session helpers."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, AccountSession, utc_now

COOKIE_NAME = "spotwise_session"
SESSION_DAYS = max(1, min(90, int(os.getenv("SPOTWISE_SESSION_DAYS", "30"))))
PBKDF2_ROUNDS = 600_000


def normalize_email(email: str) -> str:
    value = email.strip().casefold()
    if len(value) > 254 or value.count("@") != 1:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    local, domain = value.split("@", 1)
    if not local or "." not in domain or any(char.isspace() for char in value):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_session(session: Session, account: Account) -> str:
    token = secrets.token_urlsafe(36)
    session.add(AccountSession(
        account_id=account.id,
        token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
        expires_at=utc_now() + timedelta(days=SESSION_DAYS),
    ))
    return token


def get_account_from_request(request: Request, session: Session) -> Account | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    record = session.scalar(select(AccountSession).where(AccountSession.token_hash == token_hash))
    if record is None:
        return None
    if record.expires_at <= utc_now():
        session.delete(record)
        session.commit()
        return None
    return session.get(Account, record.account_id)


def set_session_cookie(response: Response, token: str, request: Request) -> None:
    secure = request.url.scheme == "https" or os.getenv("SPOTWISE_SECURE_COOKIES", "").casefold() in {"1", "true", "yes"}
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    secure = request.url.scheme == "https" or os.getenv("SPOTWISE_SECURE_COOKIES", "").casefold() in {"1", "true", "yes"}
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=secure, samesite="lax")


def require_account(account: Account | None) -> Account:
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in or create a SpotWise account to access saved studies and imported datasets.",
        )
    return account
