import os
import time
import jwt
from jwt import PyJWKClient
from fastapi import Header, HTTPException

def _get_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return authorization.split(" ", 1)[1]

def _get_jwks_url() -> str:
    return os.getenv("CLERK_JWKS_URL", "https://api.clerk.com/v1/jwks")

def _get_allowed_origins() -> list[str]:
    return [
        o.strip()
        for o in os.getenv("CLERK_ALLOWED_ORIGINS", "").split(",")
        if o.strip()
    ]

def _get_jwk_client() -> PyJWKClient:
    # Create client lazily so env vars are definitely loaded first
    jwks_url = _get_jwks_url()
    return PyJWKClient(jwks_url)

async def require_clerk_user_id(authorization: str | None = Header(default=None)) -> str:
    token = _get_bearer_token(authorization)

    jwks_url = _get_jwks_url()
    allowed_origins = _get_allowed_origins()

    try:
        jwk_client = _get_jwk_client()
        signing_key = jwk_client.get_signing_key_from_jwt(token).key

        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )

        now = int(time.time())
        if "exp" in claims and claims["exp"] < now:
            raise HTTPException(status_code=401, detail="Token expired")
        if "nbf" in claims and claims["nbf"] > now:
            raise HTTPException(status_code=401, detail="Token not valid yet")

        # Optional azp check
        if allowed_origins:
            azp = claims.get("azp")
            if azp and azp not in allowed_origins:
                raise HTTPException(status_code=401, detail=f"Invalid azp: {azp}")

        clerk_user_id = claims.get("sub")
        if not clerk_user_id:
            raise HTTPException(status_code=401, detail="No user id (sub) in token")

        return clerk_user_id

    except HTTPException:
        raise
    except Exception as e:
        # This will show you if you're STILL hitting a forbidden/403 JWKS URL
        print("[AUTH] Using JWKS_URL:", jwks_url)
        print("[AUTH] Allowed origins:", allowed_origins)
        print("[AUTH] Token verification failed:", repr(e))
        raise HTTPException(status_code=401, detail=f"Invalid token: {type(e).__name__}")
