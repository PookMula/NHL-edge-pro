"""Client for the public NHL web API (api-web.nhle.com).

Polite by default: a minimum interval between requests, exponential backoff on 429/5xx,
and explicit errors (never silent fallbacks to invented data).
"""
import json
import time
import urllib.error
import urllib.request

BASE = "https://api-web.nhle.com/v1"
SOURCE_NAME = "NHL API (api-web.nhle.com)"


class NHLApiError(RuntimeError):
    pass


class NHLClient:
    def __init__(self, min_interval=0.3, retries=5, timeout=25):
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0
        self.calls = 0

    def get(self, path):
        url = f"{BASE}/{path.lstrip('/')}"
        last_err = None
        for attempt in range(self.retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "nhl-edge-pro/0.1 (personal research)",
                    "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self.calls += 1
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 404:
                    raise NHLApiError(f"404 Not Found: {url}") from e
                if e.code == 429 or e.code >= 500:
                    time.sleep(min(60, 2 ** attempt * 2))
                    continue
                raise NHLApiError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(min(30, 2 ** attempt))
        raise NHLApiError(f"giving up on {url}: {last_err}")

    def schedule(self, date_str):
        return self.get(f"schedule/{date_str}")

    def boxscore(self, game_id):
        return self.get(f"gamecenter/{game_id}/boxscore")
