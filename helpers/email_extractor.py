"""Extract emails from business websites."""

import logging
import re

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Match mailto: 
_MAILTO_RE = re.compile(
    r'''mailto\s*:\s*([^"'\s<>]+)''',
    re.IGNORECASE,
)

# Common false positives to drop.
_BAD_EXTS = frozenset(
    (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js",
     ".json", ".xml", ".pdf", ".zip", ".mp4", ".mp3")
)

# Domains that are never business contact emails.
_BAD_DOMAINS = frozenset(
    ("sentry.io", "sentry.wixpress.com", "sentry-next.wixpress.com",
     "example.com", "domain.com", "domaine.com", "myname.com",
     "test.com", "sample.com", "localhost")
)

# Known template/placeholder email patterns (local@domain).
_TEMPLATE_EMAILS = frozenset(
    ("name@myname.com", "nom@domain.com", "nom@domaine.com",
     "utilisateur@domaine.com", "user@domain.com", "email@example.com",
     "yourname@domain.com", "username@domain.com")
)


def _is_valid_email(email: str) -> bool:
    """Quick sanity check for an email address."""
    if not email or len(email) > 254:
        return False
    if email.startswith(".") or email.endswith(".") or ".." in email:
        return False
    if email.count("@") != 1:
        return False
    local, domain = email.split("@", 1)
    if not local or not domain:
        return False
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return False
    low = email.lower()
    if any(low.endswith(ext) for ext in _BAD_EXTS):
        return False
    if low in _TEMPLATE_EMAILS:
        return False
    if domain.lower() in _BAD_DOMAINS:
        return False
    # Reject strings that look like image/asset URLs
    if "/" in local:
        return False
    return True


def _extract_from_mailto(html: str) -> list[str]:
    """Pull emails out of mailto: links."""
    found = []
    for m in _MAILTO_RE.finditer(html):
        candidate = m.group(1).strip()
        # mailto may contain ?subject=... or other params
        candidate = candidate.split("?")[0].split("&")[0]
        candidate = candidate.strip()
        if _is_valid_email(candidate):
            found.append(candidate.lower())
    return found


def _extract_from_regex(html: str) -> list[str]:
    """Pull emails via regex from the raw HTML."""
    found = []
    for m in _EMAIL_RE.finditer(html):
        candidate = m.group(0).strip()
        if _is_valid_email(candidate):
            found.append(candidate.lower())
    return found


def extract_emails(url: str, fetch_fn, timeout: int = 15) -> list[str]:

    if not url or not url.startswith("http"):
        return []

    try:
        resp = fetch_fn(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.5",
            },
        )
        if resp.status_code != 200:
            logger.debug("Email fetch returned HTTP %d for %s", resp.status_code, url)
            return []
        html = resp.text
    except Exception as exc:
        logger.debug("Email fetch failed for %s: %s", url, exc)
        return []

    emails = _extract_from_mailto(html)
    if not emails:
        emails = _extract_from_regex(html)

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for email in emails:
        if email not in seen:
            seen.add(email)
            result.append(email)
    return result
