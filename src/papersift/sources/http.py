from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "papersift/0.1"
RETRY_ON = (429, 500, 502, 503, 504)


class SourceError(Exception):
    pass


def get(url: str, params: dict | None = None, tries: int = 4) -> bytes:
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(clean)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            if err.code not in RETRY_ON or attempt == tries - 1:
                detail = err.read().decode("utf-8", "replace")[:300]
                raise SourceError(f"{err.code} from {urllib.parse.urlsplit(url).netloc}: {detail}") from err
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt == tries - 1:
                raise SourceError(f"Could not reach {urllib.parse.urlsplit(url).netloc}: {err}") from err
        time.sleep(2 ** attempt * 2)
    raise SourceError("unreachable")
