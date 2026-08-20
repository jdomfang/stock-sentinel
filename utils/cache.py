"""Process-local memoisation that does not need Streamlit. Standard library only.

WHY THIS EXISTS

Two functions in the analysis path were memoised with @st.cache_data and
@st.cache_resource. Those decorators are evaluated at import, so the modules
holding them could not be imported at all outside the portal -- which is what
kept utils/finance.py and utils/sentiment.py inside the Streamlit process.

BEHAVIOURALLY EQUIVALENT FOR THESE CALL SITES, and worth being precise about
why, since "we replaced the cache" is exactly the kind of change that quietly
alters a product:

  Both decorated functions take NO arguments, so there is no keying to get
  wrong. Streamlit's caches are per-process and shared across sessions and
  reruns; a module-level dict is per-process and shared across sessions and
  reruns. For a zero-argument function the two are the same object lifetime.

  What is NOT reproduced, and matters if a caller ever mutates a cached value:
  st.cache_data unpickles a FRESH COPY on every read, so a caller mutating the
  result cannot corrupt the cache. These return the SHARED object. No current
  caller mutates (the one value is a ticker-master dict that is only read), but
  a future one would need a copy.

  Also not reproduced: argument hashing. Both decorated functions are nullary
  and calling them with arguments raises rather than silently returning another
  caller's value. This is NOT a drop-in for the general case.

  AND THE DECORATOR MUST BE APPLIED AT MODULE SCOPE. Streamlit keys its cache on
  (module, qualname) in a global store, so decorating a NESTED function still
  hit one entry. These keep state in a closure, so re-decorating on each call
  builds a fresh empty cache -- a dead memo that looks exactly like a live one.
  Measured when that happened here: five calls, five full table loads.

THREAD SAFETY. Deep Analyze fetches its two corpora on a ThreadPoolExecutor, so
these can be entered concurrently. A lock makes the miss path single-flight --
without it two threads both miss, both compute, and for the model singleton that
means loading ~1 GB twice.
"""

from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable

# PER FUNCTION, not one global. A single module lock meant load_sentiment_pipeline
# held it for the whole ~1 GB model load and blocked every other cached miss in
# the process -- and any cached function calling another would deadlock on a
# non-reentrant lock. Streamlit's caches are per-function for the same reason.


def ttl_cache(seconds: float) -> Callable:
    """Memoise a zero-argument function for `seconds`.

    Replaces @st.cache_data(ttl=...) on nullary functions.
    """
    def decorate(fn: Callable) -> Callable:
        box: dict[str, Any] = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if a or kw:
                raise TypeError(
                    f"{fn.__name__} is cached without argument keying; "
                    "calling it with arguments would silently return another "
                    "caller's value")
            now = time.time()
            hit = box.get("v")
            if hit is not None and now < hit[1]:
                return hit[0]
            with lock:
                # Re-check: another thread may have filled it while we waited.
                hit = box.get("v")
                if hit is not None and time.time() < hit[1]:
                    return hit[0]
                val = fn()
                box["v"] = (val, time.time() + seconds)
                return val

        wrapper.cache_clear = lambda: box.pop("v", None)   # type: ignore[attr-defined]
        return wrapper
    return decorate


def singleton(fn: Callable) -> Callable:
    """Compute once per process, keep forever. Replaces @st.cache_resource.

    Single-flight on the miss: the one caller here loads a transformer model,
    and two threads racing it would hold two copies resident.
    """
    box: dict[str, Any] = {}
    lock = threading.Lock()

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if a or kw:
            raise TypeError(f"{fn.__name__} is a singleton and takes no arguments")
        if "v" in box:
            return box["v"]
        with lock:
            if "v" not in box:
                box["v"] = fn()
            return box["v"]

    wrapper.cache_clear = lambda: box.pop("v", None)        # type: ignore[attr-defined]
    return wrapper
