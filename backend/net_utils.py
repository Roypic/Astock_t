from __future__ import annotations

import ssl
import urllib.request
from typing import Any


def _default_ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def safe_urlopen(request: str | urllib.request.Request, timeout: float = 10, **kwargs: Any):
    context = kwargs.pop("context", None) or _default_ssl_context()
    try:
        if context is not None:
            return urllib.request.urlopen(request, timeout=timeout, context=context, **kwargs)
        return urllib.request.urlopen(request, timeout=timeout, **kwargs)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return urllib.request.urlopen(request, timeout=timeout, context=ssl._create_unverified_context(), **kwargs)
        raise
