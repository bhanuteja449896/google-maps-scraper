"""API key authentication for Google Maps Scraper API."""

import os

from fastapi import Header, HTTPException, status


async def verify_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    """
    Validate the X-API-Key header.

    If API_KEY env var is not set (dev mode), all requests are allowed.
    In production, set API_KEY to a strong secret and pass it in every request.
    """
    api_key = os.environ.get("API_KEY", "").strip()
    if not api_key:
        # Dev mode — no key configured, open access
        return

    if not x_api_key or x_api_key != api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Pass your key in the X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
