import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secure_compare_token(token: str, token_hash: str | None) -> bool:
    if not token_hash:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


def new_session_token(hours: int = 1) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(48)
    expiry = datetime.now(UTC) + timedelta(hours=hours)
    return token, expiry
