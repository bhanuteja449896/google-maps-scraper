"""Pydantic request/response models for the Google Maps Scraper API."""

from typing import Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Request body for POST /scrape/search — starts a search + scrape job."""

    # ── Required ──────────────────────────────────────────────────────────────
    query: str = Field(
        ...,
        description="Google Maps search query",
        examples=["Restaurants in Mumbai", "Hotels near Eiffel Tower"],
    )

    # ── Location (optional bias) ───────────────────────────────────────────────
    lat: float = Field(0.0, description="Latitude bias for search center point")
    lng: float = Field(0.0, description="Longitude bias for search center point")
    zoom: int = Field(13, description="Zoom level for search grid (higher = tighter area)", ge=1, le=21)

    # ── Scrape limits ──────────────────────────────────────────────────────────
    max_places: int = Field(
        250,
        description="Max number of places to scrape. Default 250. Send any number to override.",
        ge=1,
    )
    max_reviews: int = Field(
        0,
        description=(
            "Max reviews to fetch per place. "
            "Default 0 = skip reviews. "
            "Send a number (e.g. 100) to fetch that many reviews per place. "
            "Send a very large number (e.g. 99999) to fetch ALL reviews."
        ),
        ge=0,
    )

    # ── Bot-bypass / concurrency ───────────────────────────────────────────────
    concurrent_workers: int = Field(
        4,
        alias="workers",
        description=(
            "Number of concurrent scraping sessions (1–16). "
            "Each worker uses an independent browser session to bypass bot detection. "
            "Higher = faster but more detectable. Default 4 is the sweet spot."
        ),
        ge=1,
        le=16,
    )
    request_delay: float = Field(
        2.5,
        alias="delay",
        description=(
            "Minimum delay in seconds between requests per worker. "
            "Higher delay = less detectable but slower. Min 0.5s."
        ),
        ge=0.5,
    )
    proxy_url: Optional[str] = Field(
        None,
        alias="proxy",
        description=(
            "Optional proxy URL to route all requests through. "
            "Supports HTTP and SOCKS5. "
            "Examples: 'http://user:pass@host:port' or 'socks5://host:port'"
        ),
        examples=["http://user:pass@proxy.example.com:8080", "socks5://127.0.0.1:1080"],
    )

    # ── Output options ─────────────────────────────────────────────────────────
    extract_emails: bool = Field(
        False,
        description=(
            "If true, visits each place's website to extract email addresses "
            "not provided in Google Maps data. Slower but more complete."
        ),
    )

    # ── Localisation ──────────────────────────────────────────────────────────
    lang: str = Field("en", description="Language code for results (e.g. 'en', 'hi', 'ar')")
    gl: str = Field(
        "us",
        description="Country code for results (e.g. 'in' for India, 'us' for USA, 'gb' for UK)",
    )

    # ── Advanced ──────────────────────────────────────────────────────────────
    job_id: Optional[str] = Field(
        None,
        description=(
            "Custom job ID for tracking. Auto-generated (16-char hex) if omitted. "
            "Useful if you want a predictable ID from your frontend."
        ),
    )

    model_config = {"populate_by_name": True}


class PlaceRequest(BaseModel):
    """Request body for POST /scrape/place — scrapes a single Google Maps place."""

    # ── Required ──────────────────────────────────────────────────────────────
    place_id: str = Field(
        ...,
        description="Google Maps place ID (starts with 'ChIJ...')",
        examples=["ChIJN1t_tDeuEmsRUsoyG83frY4"],
    )

    # ── Location hint ──────────────────────────────────────────────────────────
    lat: float = Field(0.0, description="Latitude hint for better result accuracy")
    lng: float = Field(0.0, description="Longitude hint for better result accuracy")

    # ── Scrape limits ──────────────────────────────────────────────────────────
    max_reviews: int = Field(
        0,
        description=(
            "Max reviews to fetch. "
            "Default 0 = skip reviews. "
            "Send a number to fetch that many reviews."
        ),
        ge=0,
    )

    # ── Bot-bypass / concurrency ───────────────────────────────────────────────
    concurrent_workers: int = Field(
        4,
        alias="workers",
        description="Concurrent scraping sessions (1–16). Default 4.",
        ge=1,
        le=16,
    )
    request_delay: float = Field(
        2.5,
        alias="delay",
        description="Min delay in seconds between requests. Default 2.5s.",
        ge=0.5,
    )
    proxy_url: Optional[str] = Field(
        None,
        alias="proxy",
        description="Optional proxy URL (http:// or socks5://)",
    )

    # ── Output options ─────────────────────────────────────────────────────────
    extract_emails: bool = Field(False, description="Visit website to extract email addresses")

    # ── Localisation ──────────────────────────────────────────────────────────
    lang: str = Field("en", description="Language code (e.g. 'en', 'hi')")
    gl: str = Field("us", description="Country code (e.g. 'in', 'us')")

    model_config = {"populate_by_name": True}


class ResumeRequest(BaseModel):
    """Request body for POST /scrape/resume — resumes an interrupted job."""

    job_id: str = Field(..., description="The job ID to resume (must exist in Google Sheets state)")
    max_reviews: Optional[int] = Field(
        None,
        description="Override max reviews for the resumed job. Omit to keep original setting.",
        ge=0,
    )
