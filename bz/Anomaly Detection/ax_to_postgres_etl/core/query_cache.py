"""Query result caching for ETL operations."""

import time
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Optional, Any, Callable
from functools import wraps


@dataclass
class CacheEntry:
    """A cached query result."""
    key: str
    value: Any
    created_at: float
    ttl_seconds: int
    hit_count: int = 0

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl_seconds

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class QueryCache:
    """
    In-memory cache for query results.

    Useful for:
    - Caching schema info
    - Caching table boundaries
    - Caching row counts
    """

    def __init__(self, default_ttl: int = 300, max_entries: int = 1000):
        """
        Args:
            default_ttl: Default TTL in seconds
            max_entries: Maximum cache entries
        """
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self._cache: dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    def _make_key(self, prefix: str, *args, **kwargs) -> str:
        """Generate cache key from arguments."""
        key_parts = [prefix] + [str(a) for a in args]
        if kwargs:
            key_parts.append(json.dumps(kwargs, sort_keys=True, default=str))
        raw = ":".join(key_parts)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None

        if entry.is_expired:
            del self._cache[key]
            self._misses += 1
            return None

        entry.hit_count += 1
        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in cache."""
        # Evict oldest if at capacity
        if len(self._cache) >= self.max_entries:
            oldest_key = min(self._cache, key=lambda k: self._cache[k].created_at)
            del self._cache[oldest_key]

        self._cache[key] = CacheEntry(
            key=key,
            value=value,
            created_at=time.time(),
            ttl_seconds=ttl or self.default_ttl,
        )

    def invalidate(self, key: str):
        """Remove entry from cache."""
        self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str):
        """Remove all entries with given prefix."""
        keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._cache[k]

    def clear(self):
        """Clear all cache entries."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "entries": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total * 100, 1) if total > 0 else 0,
        }

    def cached(self, prefix: str, ttl: Optional[int] = None):
        """Decorator for caching function results."""
        def decorator(func: Callable):
            @wraps(func)
            def wrapper(*args, **kwargs):
                key = self._make_key(prefix, *args, **kwargs)
                result = self.get(key)
                if result is not None:
                    return result
                result = func(*args, **kwargs)
                self.set(key, result, ttl)
                return result
            return wrapper
        return decorator


class DiskCache:
    """
    Disk-based cache for larger datasets.

    Useful for caching:
    - Schema information
    - Large query results
    - Historical data
    """

    def __init__(self, cache_dir: str = ".cache", default_ttl: int = 3600):
        self.cache_dir = cache_dir
        self.default_ttl = default_ttl
        os.makedirs(cache_dir, exist_ok=True)

    def _get_path(self, key: str) -> str:
        safe_key = hashlib.md5(key.encode()).hexdigest()
        return os.path.join(self.cache_dir, f"{safe_key}.json")

    def get(self, key: str) -> Optional[Any]:
        """Get value from disk cache."""
        path = self._get_path(key)
        if not os.path.exists(path):
            return None

        try:
            with open(path, "r") as f:
                data = json.load(f)

            if time.time() - data.get("created_at", 0) > data.get("ttl", self.default_ttl):
                os.remove(path)
                return None

            return data.get("value")
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in disk cache."""
        path = self._get_path(key)
        data = {
            "key": key,
            "value": value,
            "created_at": time.time(),
            "ttl": ttl or self.default_ttl,
        }
        with open(path, "w") as f:
            json.dump(data, f, default=str)

    def invalidate(self, key: str):
        """Remove entry from disk cache."""
        path = self._get_path(key)
        if os.path.exists(path):
            os.remove(path)

    def clear(self):
        """Clear all disk cache entries."""
        for f in os.listdir(self.cache_dir):
            if f.endswith(".json"):
                os.remove(os.path.join(self.cache_dir, f))
