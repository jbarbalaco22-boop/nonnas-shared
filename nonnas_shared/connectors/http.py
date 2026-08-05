"""Shared HTTP helper for calls to Intuit/Shopify APIs.

Captures the intuit_tid response header (Intuit's own recommended field for
support/troubleshooting) and logs full error details — status code,
intuit_tid, response body — on any failed request, so error information is
never silently lost. Callers still get the raised exception; this only adds
logging on top.
"""
import logging

import requests

logger = logging.getLogger("nonnas_shared.http")


def request(method: str, url: str, **kwargs) -> requests.Response:
    resp = requests.request(method, url, **kwargs)
    intuit_tid = resp.headers.get("intuit_tid")
    if not resp.ok:
        logger.error(
            "API error: %s %s -> %s (intuit_tid=%s) body=%s",
            method, url, resp.status_code, intuit_tid, resp.text[:2000],
        )
    else:
        logger.debug("%s %s -> %s (intuit_tid=%s)", method, url, resp.status_code, intuit_tid)
    return resp
