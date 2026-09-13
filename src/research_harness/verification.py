"""Bounded memoization of read-only checks over settled local artifact files.

Callers retain their existing locks, validators and live-state comparisons. This
cache trusts host-maintained POSIX metadata, not a candidate-provided inventory.
Public audits can continue to call their verifiers without a memo. Entries never
survive a process restart and recent/unsupported metadata always takes the full path.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import TypeVar

T = TypeVar("T")
_SETTLE_NS = 2_000_000_000
_MAX_SCAN_ENTRIES = 65536


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _snapshot(roots: tuple[Path, ...]) -> tuple[tuple, int] | None:
    if os.name != "posix":  # Windows ctime can describe creation, not a content change.
        return None
    rows, ancestors, newest = [], set(), 0
    try:
        for root in roots:
            for parent in root.parents:
                if parent in ancestors:
                    continue
                ancestors.add(parent)
                info = parent.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    return None
                # Sibling writes do not change the identity of a path's ancestors.
                rows.append(
                    (
                        "ancestor",
                        str(parent),
                        info.st_dev,
                        info.st_ino,
                        info.st_mode,
                        info.st_uid,
                        info.st_gid,
                    )
                )
            pending = [root]
            while pending:
                path = pending.pop()
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    with os.scandir(path) as children:
                        for child in children:
                            pending.append(path / child.name)
                            if len(rows) + len(pending) > _MAX_SCAN_ENTRIES:
                                return None
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    return None
                rows.append(("entry", str(path), *_identity(info)))
                newest = max(newest, info.st_mtime_ns, info.st_ctime_ns)
                if len(rows) + len(pending) > _MAX_SCAN_ENTRIES:
                    return None
    except OSError:
        # Let the original verifier report missing files, permissions or bad paths.
        return None
    return tuple(sorted(rows)), newest


def _weight(value, seen: set[int] | None = None) -> int:
    """Account for retained built-in data; opaque objects are deliberately not cached."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if type(value) is dict:
        return size + sum(_weight(k, seen) + _weight(v, seen) for k, v in value.items())
    if type(value) in {list, tuple}:
        return size + sum(_weight(item, seen) for item in value)
    if type(value) not in {str, bytes, int, float, bool, type(None)}:
        raise TypeError("Only retained built-in verification data can be memoized")
    return size


class VerificationMemo:
    """Reuse successful pure verification only when its complete dependencies match.

    Include every non-file input in ``context`` and every file dependency in ``roots``.
    Membership and metadata are read on every lookup. A two-second settling window
    prevents reusing a result across ordinary writes within a coarse timestamp tick.
    Failed verifiers are not cached. Return values are copied to prevent cache poisoning.
    """

    def __init__(
        self,
        *,
        max_bytes: int = 32 * 1024 * 1024,
        max_entries: int = 4096,
        clock_ns: Callable[[], int] = time.time_ns,
    ):
        if max_bytes < 0 or max_entries < 0:
            raise ValueError("Memo limits must be nonnegative")
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._clock_ns = clock_ns
        self._entries: OrderedDict = OrderedDict()
        self._bytes = 0
        self._generation = 0
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0
            self._generation += 1

    def verify(
        self,
        context: Hashable,
        roots: Iterable[Path],
        compute: Callable[[], T],
        *,
        cache_if: Callable[[T], bool] | None = None,
    ) -> T:
        paths = tuple(Path(path).absolute() for path in roots)
        key = context, tuple(map(str, paths))
        before = _snapshot(paths) if self.max_bytes and self.max_entries else None
        settled = before is not None and 0 < before[1] <= self._clock_ns() - _SETTLE_NS
        with self._lock:
            generation = self._generation
            prior = self._entries.get(key)
            if (
                settled
                and prior is not None
                and prior[0] == before
                and (cache_if is None or cache_if(deepcopy(prior[1])))
            ):
                self._entries.move_to_end(key)
                return deepcopy(prior[1])
            if prior is not None:
                self._bytes -= self._entries.pop(key)[2]
        result = compute()
        if not settled or (cache_if is not None and not cache_if(result)):
            return result
        after = _snapshot(paths)
        if after != before:
            raise ValueError("Artifact metadata changed during verification")
        try:
            weight = _weight((key, after, result))
        except (TypeError, RecursionError):
            return result
        if weight > self.max_bytes:
            return result
        retained = deepcopy(result)
        with self._lock:
            if generation != self._generation:
                return result
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[2]
            while self._entries and (
                len(self._entries) >= self.max_entries or self._bytes + weight > self.max_bytes
            ):
                self._bytes -= self._entries.popitem(last=False)[1][2]
            self._entries[key] = after, retained, weight
            self._bytes += weight
        return result
