"""
Background Intelligence Workload Isolation & State Coordination.

Stage 2.1:
- Bounded worker pool with fixed concurrency and bounded FIFO queue.
- Per-key queued and in-flight deduplication.
- Per-key exponential failure cooldown and backoff using monotonic clock.
- Monotonic sequence numbering and source observation timestamp ordering.
- Non-blocking result handoff to market state coordinator (no direct worker state mutation).
- Strict UTC ISO-8601 boundary validation with explicit 'Z' or zero offset.
"""

from __future__ import annotations

import enum
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("background_intelligence")

ISO8601_UTC_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00|-00:00)$"
)


def validate_utc_iso8601_timestamp(ts: Any) -> str:
    """
    Validates that ts is a string representing a strict UTC ISO-8601 timestamp with explicit 'Z' or zero offset.
    Rejects naive, non-string, missing, invalid, or ambiguous timestamps fail-closed.
    Returns normalized ISO-8601 UTC string.
    """
    if not isinstance(ts, str):
        raise ValueError(f"Timestamp must be a string, got {type(ts).__name__}")
    ts = ts.strip()
    if not ISO8601_UTC_REGEX.match(ts):
        raise ValueError(
            f"Timestamp '{ts}' is not a strict UTC ISO-8601 timestamp with explicit Z or zero offset"
        )
    try:
        clean_ts = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            raise ValueError(f"Timestamp '{ts}' is naive (missing tzinfo)")
        if dt.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError(f"Timestamp '{ts}' has non-zero UTC offset: {dt.utcoffset()}")
    except Exception as e:
        raise ValueError(f"Invalid calendar date/time in timestamp '{ts}': {e}") from e
    return ts


class WorkType(str, enum.Enum):
    GROUNDING = "grounding"
    SCOUT = "scout"


class WorkStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    IN_FLIGHT = "IN_FLIGHT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class WorkKey:
    work_type: str
    target: str

    def __post_init__(self):
        if not isinstance(self.work_type, str) or not self.work_type.strip():
            raise ValueError("work_type must be a non-empty string")
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("target must be a non-empty string")
        object.__setattr__(self, "work_type", self.work_type.strip().lower())
        object.__setattr__(self, "target", self.target.strip().upper())


@dataclass
class WorkItem:
    key: WorkKey
    sequence: int
    obs_time: str
    fn: Callable[..., Any]
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    status: WorkStatus = WorkStatus.QUEUED
    enqueued_time: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[str] = None


@dataclass(frozen=True)
class WorkResult:
    key: WorkKey
    sequence: int
    obs_time: str
    data: Any
    error: Optional[str] = None
    completed_time: float = field(default_factory=time.monotonic)


class FailureCooldownTracker:
    """
    Tracks failure counts and exponential backoff cooldown per WorkKey.
    Uses monotonic time. Fully isolated per work key.
    """
    def __init__(
        self,
        base_delay: float = 5.0,
        backoff_factor: float = 2.0,
        max_delay: float = 300.0,
        clock_fn: Callable[[], float] = time.monotonic
    ):
        self.base_delay = float(base_delay)
        self.backoff_factor = float(backoff_factor)
        self.max_delay = float(max_delay)
        self.clock_fn = clock_fn
        self._lock = threading.Lock()
        self._failures: Dict[WorkKey, int] = {}
        self._deadlines: Dict[WorkKey, float] = {}

    def is_in_cooldown(self, key: WorkKey) -> bool:
        with self._lock:
            deadline = self._deadlines.get(key, 0.0)
            now = self.clock_fn()
            return now < deadline

    def get_cooldown_remaining(self, key: WorkKey) -> float:
        with self._lock:
            deadline = self._deadlines.get(key, 0.0)
            now = self.clock_fn()
            return max(0.0, deadline - now)

    def record_failure(self, key: WorkKey) -> float:
        with self._lock:
            now = self.clock_fn()
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            delay = min(self.base_delay * (self.backoff_factor ** (count - 1)), self.max_delay)
            deadline = now + delay
            self._deadlines[key] = deadline
            logger.warning(
                f"[Cooldown] Key {key} failed (count={count}). Backoff delay={delay:.1f}s until {deadline:.1f}"
            )
            return deadline

    def record_success(self, key: WorkKey) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._deadlines.pop(key, None)

    def get_failure_count(self, key: WorkKey) -> int:
        with self._lock:
            return self._failures.get(key, 0)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._deadlines.clear()


class BackgroundIntelligenceCoordinator:
    """
    Fixed-concurrency, bounded-queue background processor for intelligence tasks.
    Enforces per-key queued and in-flight deduplication, failure cooldown,
    monotonic sequencing, and non-blocking handoff to the market coordinator.
    """
    def __init__(
        self,
        max_workers: int = 2,
        max_queue_size: int = 50,
        cooldown_tracker: Optional[FailureCooldownTracker] = None,
        clock_fn: Callable[[], float] = time.monotonic
    ):
        self.max_workers = max(1, int(max_workers))
        self.max_queue_size = max(1, int(max_queue_size))
        self.clock_fn = clock_fn
        self.cooldown_tracker = cooldown_tracker or FailureCooldownTracker(clock_fn=clock_fn)

        self._queue: queue.Queue = queue.Queue(maxsize=self.max_queue_size)
        self._results_queue: queue.Queue = queue.Queue()

        self._lock = threading.Lock()
        self._pending_keys: Dict[WorkKey, WorkItem] = {}
        self._in_flight_keys: Dict[WorkKey, WorkItem] = {}
        self._sequences: Dict[WorkKey, int] = {}

        self._shutdown_event = threading.Event()
        self._workers: List[threading.Thread] = []

        self._start_workers()

    def _start_workers(self) -> None:
        with self._lock:
            for i in range(self.max_workers):
                t = threading.Thread(
                    target=self._worker_loop,
                    name=f"BackgroundIntelligenceWorker-{i+1}",
                    daemon=True
                )
                self._workers.append(t)
                t.start()

    def enqueue(
        self,
        work_type: str,
        target: str,
        obs_time: str,
        fn: Callable[..., Any],
        args: tuple = (),
        kwargs: Optional[dict] = None,
        force: bool = False
    ) -> Tuple[bool, Optional[WorkItem]]:
        """
        Enqueues work if not already queued, in-flight, or in cooldown.
        Returns (accepted, work_item).
        """
        key = WorkKey(work_type, target)
        validated_obs_time = validate_utc_iso8601_timestamp(obs_time)
        kwargs = kwargs or {}

        # 1. Cooldown check (before touching queue)
        if not force and self.cooldown_tracker.is_in_cooldown(key):
            remaining = self.cooldown_tracker.get_cooldown_remaining(key)
            logger.info(f"[BackgroundIntelligence] {key} skipped: in cooldown for {remaining:.1f}s")
            return False, None

        if self._shutdown_event.is_set():
            return False, None

        with self._lock:
            # 2. Deduplication check (queued)
            if key in self._pending_keys:
                existing = self._pending_keys[key]
                # Coalesce: update obs_time if incoming observation is newer
                if validated_obs_time > existing.obs_time:
                    existing.obs_time = validated_obs_time
                logger.debug(f"[BackgroundIntelligence] {key} coalesced with already QUEUED work")
                return True, existing

            # 3. Deduplication check (in-flight)
            if key in self._in_flight_keys:
                existing = self._in_flight_keys[key]
                logger.debug(f"[BackgroundIntelligence] {key} coalesced with IN_FLIGHT work")
                return True, existing

            # 4. Queue saturation check
            if self._queue.full():
                logger.warning(
                    f"[BackgroundIntelligence] Queue saturated ({self._queue.qsize()}/{self.max_queue_size}). "
                    f"Rejecting work for {key} without blocking coordinator."
                )
                return False, None

            # 5. Monotonic sequence assignment
            seq = self._sequences.get(key, 0) + 1
            self._sequences[key] = seq

            item = WorkItem(
                key=key,
                sequence=seq,
                obs_time=validated_obs_time,
                fn=fn,
                args=args,
                kwargs=kwargs,
                status=WorkStatus.QUEUED,
                enqueued_time=self.clock_fn()
            )
            self._pending_keys[key] = item

            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._pending_keys.pop(key, None)
                logger.warning(f"[BackgroundIntelligence] Queue full on put_nowait for {key}")
                return False, None

            return True, item

    def _worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                item: WorkItem = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            # Transition from QUEUED to IN_FLIGHT under lock
            with self._lock:
                self._pending_keys.pop(item.key, None)
                if self._shutdown_event.is_set():
                    item.status = WorkStatus.CANCELLED
                    item.error = "Cancelled due to coordinator shutdown"
                    item.event.set()
                    self._queue.task_done()
                    break
                self._in_flight_keys[item.key] = item
                item.status = WorkStatus.IN_FLIGHT

            # Execute work outside lock
            data = None
            err_msg = None
            try:
                data = item.fn(*item.args, **item.kwargs)
                item.status = WorkStatus.COMPLETED
                item.result = data
                self.cooldown_tracker.record_success(item.key)
            except Exception as e:
                err_msg = str(e)
                item.status = WorkStatus.FAILED
                item.error = err_msg
                self.cooldown_tracker.record_failure(item.key)
                logger.error(
                    f"[BackgroundIntelligence] Worker execution failed for {item.key}: {e}",
                    exc_info=True
                )
            finally:
                with self._lock:
                    self._in_flight_keys.pop(item.key, None)
                    item.event.set()

                # Produce result for coordinator handoff
                result = WorkResult(
                    key=item.key,
                    sequence=item.sequence,
                    obs_time=item.obs_time,
                    data=data,
                    error=err_msg,
                    completed_time=self.clock_fn()
                )
                self._results_queue.put(result)
                self._queue.task_done()

    def drain_results(self) -> List[WorkResult]:
        """Drains and returns all available WorkResults without blocking."""
        results = []
        while True:
            try:
                res = self._results_queue.get_nowait()
                results.append(res)
                self._results_queue.task_done()
            except queue.Empty:
                break
        return results

    def is_queued_or_inflight(self, key: WorkKey) -> bool:
        with self._lock:
            return key in self._pending_keys or key in self._in_flight_keys

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending_keys)

    def in_flight_count(self) -> int:
        with self._lock:
            return len(self._in_flight_keys)

    def shutdown(self, timeout: float = 2.0) -> None:
        """Gracefully shuts down workers and releases all resources."""
        self._shutdown_event.set()

        # Mark all pending and in-flight items cancelled and wake waiters
        with self._lock:
            for item in self._pending_keys.values():
                item.status = WorkStatus.CANCELLED
                item.error = "Cancelled due to shutdown"
                item.event.set()
            for item in self._in_flight_keys.values():
                item.status = WorkStatus.CANCELLED
                item.error = "Cancelled due to shutdown"
                item.event.set()
            self._pending_keys.clear()
            self._in_flight_keys.clear()

        # Drain input queue
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break

        # Join workers
        start = self.clock_fn()
        for t in self._workers:
            remaining = max(0.05, timeout - (self.clock_fn() - start))
            t.join(timeout=remaining)


class BackgroundResultApplicator:
    """
    Coordinates application of background intelligence results to coordinator state.
    Enforces monotonic sequence and observation timestamp constraints:
    - Results superseded by older/equal sequence numbers are rejected.
    - Results superseded by older observation timestamps are rejected.
    - Missing/invalid/ambiguous observation timestamps fail closed.
    - Background workers never mutate state directly.
    """
    def __init__(self):
        self._last_applied_sequence: Dict[WorkKey, int] = {}
        self._last_applied_obs_time: Dict[WorkKey, str] = {}
        self._lock = threading.Lock()

    def get_last_applied(self, key: WorkKey) -> Tuple[int, str]:
        with self._lock:
            return (
                self._last_applied_sequence.get(key, -1),
                self._last_applied_obs_time.get(key, "")
            )

    def apply_results(
        self,
        results: List[WorkResult],
        grounding_target: dict,
        scout_cache_target: dict,
        scout_category_map: dict
    ) -> List[Tuple[WorkResult, bool, str]]:
        """
        Applies results safely to provided targets under lock.
        Returns list of (result, applied, reason).
        """
        audit = []
        with self._lock:
            for r in results:
                # If worker errored, do not apply data
                if r.error is not None or r.data is None:
                    audit.append((r, False, f"Worker error: {r.error}"))
                    continue

                # 1. Validate sequence monotonicity
                last_seq = self._last_applied_sequence.get(r.key, -1)
                if r.sequence <= last_seq:
                    msg = f"Stale sequence: received {r.sequence} <= last applied {last_seq}"
                    logger.warning(f"[BackgroundResultApplicator] {r.key} rejected: {msg}")
                    audit.append((r, False, msg))
                    continue

                # 2. Validate observation timestamp monotonicity
                try:
                    obs_ts = validate_utc_iso8601_timestamp(r.obs_time)
                except Exception as e:
                    msg = f"Invalid observation timestamp: {e}"
                    logger.warning(f"[BackgroundResultApplicator] {r.key} rejected fail-closed: {msg}")
                    audit.append((r, False, msg))
                    continue

                last_obs = self._last_applied_obs_time.get(r.key, "")
                if last_obs and obs_ts < last_obs:
                    msg = f"Stale observation: received {obs_ts} < last applied {last_obs}"
                    logger.warning(f"[BackgroundResultApplicator] {r.key} rejected: {msg}")
                    audit.append((r, False, msg))
                    continue

                # 3. Apply result based on work type
                if r.key.work_type == WorkType.GROUNDING.value:
                    grounding_target[r.key.target] = r.data
                elif r.key.work_type == WorkType.SCOUT.value:
                    scout_cache_target[r.key.target] = {
                        "tickers": r.data,
                        "timestamp": time.time()
                    }
                    if isinstance(r.data, list):
                        for t in r.data:
                            if isinstance(t, str):
                                scout_category_map[t.upper()] = r.key.target
                else:
                    audit.append((r, False, f"Unknown work type: {r.key.work_type}"))
                    continue

                self._last_applied_sequence[r.key] = r.sequence
                self._last_applied_obs_time[r.key] = obs_ts
                audit.append((r, True, "Applied successfully"))

        return audit

    def reset(self) -> None:
        with self._lock:
            self._last_applied_sequence.clear()
            self._last_applied_obs_time.clear()
