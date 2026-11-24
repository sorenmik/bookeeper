from collections import OrderedDict
from threading import Lock
from time import monotonic


class Throttle:
    """Count recent sign-in attempts per key, in memory, within a sliding window."""

    def __init__(self, limit: int, window: float, max_keys: int = 10_000):
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self.attempts: OrderedDict[str, list[float]] = OrderedDict()
        self.lock = Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        times = [moment for moment in self.attempts.get(key, ()) if now - moment < self.window]
        if times:
            self.attempts[key] = times
        else:
            self.attempts.pop(key, None)
        return times

    def wait(self, key: str) -> int:
        """Seconds until the next attempt is allowed, or 0."""
        with self.lock:
            now = monotonic()
            times = self._recent(key, now)
            if len(times) < self.limit:
                return 0
            return max(1, round(times[-self.limit] + self.window - now))

    def record(self, key: str) -> None:
        with self.lock:
            self._recent(key, monotonic())
            self.attempts.setdefault(key, []).append(monotonic())
            self.attempts.move_to_end(key)
            while len(self.attempts) > self.max_keys:
                self.attempts.popitem(last=False)

    def clear(self, key: str) -> None:
        with self.lock:
            self.attempts.pop(key, None)
