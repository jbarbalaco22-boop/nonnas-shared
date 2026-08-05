"""Shared HTTP helper for calls to Intuit/Shopify APIs.

Retries transient failures (connection errors, 429/500/502/503/504) with
backoff — auth/validation errors (4xx other than 429) are not retried since
retrying them can't succeed. Captures the intuit_tid response header
(Intuit's own recommended field for support/troubleshooting) and logs full
error details — status code, intuit_tid, response body — on any failed
request, so error information is never silently lost. Callers still get the
raised exception; this only adds retries and logging on top.
"""
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("nonnas_shared.http")

_session = requests.Session()
_retry = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
    raise_on_status=False,  # let our own raise_for_status() calls handle it, consistent error type
)
_adapter = HTTPAdapter(max_retries=_retry)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def request(method: str, url: str, **kwargs) -> requests.Response:
    resp = _session.request(method, url, **kwargs)
    intuit_tid = resp.headers.get("intuit_tid")
    if not resp.ok:
        logger.error(
            "API error: %s %s -> %s (intuit_tid=%s) body=%s",
            method, url, resp.status_code, intuit_tid, resp.text[:2000],
        )
    else:
        logger.debug("%s %s -> %s (intuit_tid=%s)", method, url, resp.status_code, intuit_tid)
    return resp
