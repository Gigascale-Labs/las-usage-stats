"""Shared HTTP session helper: custom User-Agent, retries, and polite rate limiting."""
from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "las-usage-stats/0.1 (+https://largeagentsystems.org; "
    "contact: stephen.elliott231@gmail.com) research-scraper"
)


def make_session(extra_headers: dict | None = None) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    if extra_headers:
        session.headers.update(extra_headers)
    return session


def polite_get(session: requests.Session, url: str, *, sleep: float = 0.5, **kwargs) -> requests.Response:
    """GET with a small fixed delay after the call, so loops don't hammer an endpoint."""
    resp = session.get(url, timeout=30, **kwargs)
    time.sleep(sleep)
    return resp


def polite_post(session: requests.Session, url: str, *, sleep: float = 0.5, **kwargs) -> requests.Response:
    resp = session.post(url, timeout=30, **kwargs)
    time.sleep(sleep)
    return resp
