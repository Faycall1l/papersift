from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "papersift/0.1"
RETRY_ON = (429, 500, 502, 503, 504)


class SourceError(Exception):
    pass


def get(url: str, params: dict | None = None, tries: int = 4, headers: dict | None = None,
        data: bytes | None = None, method: str | None = None) -> bytes:
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(clean)
    host = urllib.parse.urlsplit(url).netloc
    request = urllib.request.Request(
        url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            if err.code not in RETRY_ON or attempt == tries - 1:
                detail = err.read().decode("utf-8", "replace")[:300]
                raise SourceError(f"{err.code} from {host}: {detail}") from err
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt == tries - 1:
                raise SourceError(f"Could not reach {host}: {err}") from err
        time.sleep(2 ** attempt * 2)
    raise SourceError(f"Could not reach {host}")
