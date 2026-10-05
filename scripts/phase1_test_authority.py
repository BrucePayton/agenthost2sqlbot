import json
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Query
from jwt.algorithms import RSAAlgorithm

app = FastAPI()
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_jwk = json.loads(RSAAlgorithm.to_jwk(_private_key.public_key()))
_jwk.update({"kid": "phase1-key", "use": "sig", "alg": "RS256"})
_revoked: set[str] = set()


@app.get("/.well-known/jwks.json")
async def jwks() -> dict[str, object]:
    return {"keys": [_jwk]}


@app.get("/token")
async def token(
    issuer: str = Query(...),
    subject: str = Query("phase1-owner"),
) -> dict[str, str]:
    now = datetime.now(UTC)
    encoded = jwt.encode(
        {
            "iss": issuer,
            "aud": "workspace-agent",
            "sub": subject,
            "name": subject,
            "iat": now,
            "nbf": now - timedelta(seconds=1),
            "exp": now + timedelta(minutes=10),
        },
        _private_key,
        algorithm="RS256",
        headers={"kid": "phase1-key"},
    )
    return {"access_token": encoded}


@app.get("/v1/spaces/{workspace_id}/members/{subject}")
async def membership(workspace_id: str, subject: str) -> dict[str, str]:
    if workspace_id != "example" or subject in _revoked:
        raise HTTPException(status_code=404)
    return {
        "role": "owner" if subject == "phase1-owner" else "member",
        "source_version": "revoked" if subject in _revoked else "v1",
    }


@app.post("/revoke/{subject}")
async def revoke(subject: str) -> dict[str, str]:
    _revoked.add(subject)
    return {"status": "revoked"}
