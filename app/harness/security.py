import hashlib
import hmac

from app.config import settings


def ptc_token(run_id: str) -> str:
    sig = hmac.new(settings.ptc_shared_secret.encode(), run_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{run_id}.{sig}"


def verify_ptc_token(token: str) -> str | None:
    """Returns the run_id if the token is valid."""
    try:
        run_id, _ = token.split(".", 1)
    except ValueError:
        return None
    return run_id if hmac.compare_digest(ptc_token(run_id), token) else None
