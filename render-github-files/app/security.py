from __future__ import annotations
from fastapi import Header, HTTPException
from .config import API_KEY

def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-KEY")):
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
