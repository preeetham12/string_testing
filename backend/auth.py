"""
Part 5 - Login and access control.

Passwords are stored as salted PBKDF2 hashes (never as plain text). Login
returns a random token the app sends with every request:
    Authorization: Bearer <token>
Production would add token expiry and sign-in via the school's SSO.
"""
import hashlib
import hmac
import os
import secrets

from fastapi import Depends, Header, HTTPException

from backend.db import SessionLocal, Token, User

ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, digest = stored.split("$")
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return hmac.compare_digest(test.hex(), digest)  # constant-time comparison


def new_token(db, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Token(token=token, user_id=user_id))
    db.commit()
    return token


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(authorization: str = Header(None), db=Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please log in")
    tok = db.get(Token, authorization.removeprefix("Bearer "))
    if not tok:
        raise HTTPException(401, "Session expired, please log in again")
    return db.get(User, tok.user_id)


def teacher_only(user: User = Depends(current_user)) -> User:
    if user.role != "teacher":
        raise HTTPException(403, "Only teachers can do this")
    return user
