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
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)


def validate_utc_iso8601_timestamp(ts: Any) -> str:
    """
    Validates that ts is a string representing a strict UTC ISO-8601 timestamp with explicit 'Z' or zero offset (+00:00).
    Rejects naive, non-string, missing, invalid, or ambiguous timestamps (-00:00) fail-closed.
    Returns the exact valid ISO-8601 UTC string without silent normalization.
    """
    if not isinstance(ts, str):
        raise ValueError(f"Timestamp must be a string, got {type(ts).__name__}")
    ts_clean = ts.strip()
    if not ts_clean:
        raise ValueError("Timestamp cannot be empty")
    if "-00:00" in ts_clean:
        raise ValueError(
            f"Ambiguous timestamp offset '-00:00' in '{ts}': RFC3339 denotes unknown local offset; strict UTC certainty required"
        )
    if not ISO8601_UTC_REGEX.match(ts_clean):
        raise ValueError(
            f"Timestamp '{ts}' is not a strict UTC ISO-8601 timestamp with explicit Z or zero offset (+00:00)"
        )
    try:
        dt = datetime.fromisoformat(ts_clean)
        if dt.tzinfo is None:
            raise ValueError(f"Timestamp '{ts}' is naive (missing tzinfo)")
        if dt.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError(f"Timestamp '{ts}' has non-zero UTC offset: {dt.utcoffset()}")
    except Exception as e:
        raise ValueError(f"Invalid calendar date/time in timestamp '{ts}': {e}") from e
    return ts_clean


def parse_utc_iso8601_timestamp(ts: Any) -> datetime:
    """
    Strictly validates and parses a UTC ISO-8601 timestamp into a timezone-aware UTC datetime.
    """
    valid_str = validate_utc_iso8601_timestamp(ts)
    dt = datetime.fromisoformat(valid_str)
    return dt.astimezone(timezone.utc)


class WorkType(str, enum.Enum):
    GROUNDING = "grounding"
    SCOUT = "scout"
    MARKET_DATA = "market_data"


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
    obs_time: Optional[str] = None
    fn: Callable[..., Any] = None
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    status: WorkStatus = WorkStatus.QUEUED
    enqueued_time: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[str] = None


@dataclass(frozen=True)
class WorkResult:
    key: WorkKey
    sequence: int
    obs_time: Optional[str] = None
    data: Any = None
    error: Optional[str] = None
    completed_time: float = field(default_factory=time.monotonic)
    enqueued_time: Optional[float] = None


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
    monotonic sequencing, bounded result handoff, and non-blocking coordinator operation.
    """
    def __init__(
        self,
        max_workers: int = 2,
        max_queue_size: int = 50,
        max_results_size: int = 50,
        cooldown_tracker: Optional[FailureCooldownTracker] = None,
        clock_fn: Callable[[], float] = time.monotonic
    ):
        self.max_workers = max(1, int(max_workers))
        self.max_queue_size = max(1, int(max_queue_size))
        self.max_results_size = max(1, int(max_results_size))
        self.clock_fn = clock_fn
        self.cooldown_tracker = cooldown_tracker or FailureCooldownTracker(clock_fn=clock_fn)

        self._queue: queue.Queue = queue.Queue(maxsize=self.max_queue_size)
        self._results_queue: queue.Queue = queue.Queue(maxsize=self.max_results_size)
        self._overflow_results: Dict[WorkKey, WorkResult] = {}

        self._lock = threading.RLock()
        self._pending_keys: Dict[WorkKey, WorkItem] = {}
        self._in_flight_keys: Dict[WorkKey, WorkItem] = {}
        self._sequences: Dict[WorkKey, int] = {}
        self._latest_issued_sequence: Dict[WorkKey, int] = {}
        self._key_locks: Dict[WorkKey, threading.RLock] = {}

        self._shutdown_event = threading.Event()
        self._workers: List[threading.Thread] = []

        self._start_workers()

    def get_key_lock(self, key: WorkKey) -> threading.RLock:
        """Returns per-key reentrant lock ensuring atomicity of sequence issuance and publication."""
        with self._lock:
            if key not in self._key_locks:
                self._key_locks[key] = threading.RLock()
            return self._key_locks[key]

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

    def get_latest_issued_sequence(self, key: WorkKey) -> int:
        with self._lock:
            return self._latest_issued_sequence.get(key, 0)

    def total_work_count(self) -> int:
        with self._lock:
            return (
                len(self._pending_keys)
                + len(self._in_flight_keys)
                + self._results_queue.qsize()
                + len(self._overflow_results)
            )

    def cancel_work(self, key: WorkKey) -> bool:
        """
        Cancels work for key if currently queued or in-flight.
        Propagates cancellation into running workers.
        Returns True if work was found and cancellation was requested, False otherwise.
        """
        key_lock = self.get_key_lock(key)
        with key_lock:
            with self._lock:
                if key in self._pending_keys:
                    item = self._pending_keys.pop(key)
                    item.cancel_event.set()
                    item.status = WorkStatus.CANCELLED
                    item.error = "Work cancelled while queued"
                    item.event.set()
                    return True
                if key in self._in_flight_keys:
                    item = self._in_flight_keys[key]
                    item.cancel_event.set()
                    return True
                return False

    def enqueue(
        self,
        work_type: str,
        target: str,
        obs_time: Optional[str] = None,
        fn: Callable[..., Any] = None,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        force: bool = False
    ) -> Tuple[bool, Optional[WorkItem]]:
        """
        Enqueues work if not already queued, in-flight, or in cooldown.
        Atomically assigns sequence under per-key lock.
        Separates source observation timestamp from monotonic enqueue time.
        Returns (accepted, work_item).
        """
        if self._shutdown_event.is_set():
            return False, None

        key = WorkKey(work_type, target)
        validated_obs_time = None
        if obs_time is not None:
            validated_obs_time = validate_utc_iso8601_timestamp(obs_time)
        kwargs = kwargs or {}

        # 1. Cooldown check (before touching queue)
        if not force and self.cooldown_tracker.is_in_cooldown(key):
            remaining = self.cooldown_tracker.get_cooldown_remaining(key)
            logger.info(f"[BackgroundIntelligence] {key} skipped: in cooldown for {remaining:.1f}s")
            return False, None

        key_lock = self.get_key_lock(key)
        with key_lock:
            with self._lock:
                if self._shutdown_event.is_set():
                    return False, None

                # 2. Deduplication check (queued)
                if key in self._pending_keys:
                    existing = self._pending_keys[key]
                    # Chronological instant comparison when timestamps are present
                    if validated_obs_time is not None and existing.obs_time is not None:
                        incoming_dt = parse_utc_iso8601_timestamp(validated_obs_time)
                        existing_dt = parse_utc_iso8601_timestamp(existing.obs_time)
                        if incoming_dt > existing_dt:
                            existing.obs_time = validated_obs_time
                    elif validated_obs_time is not None and existing.obs_time is None:
                        existing.obs_time = validated_obs_time
                    logger.debug(f"[BackgroundIntelligence] {key} coalesced with already QUEUED work")
                    return True, existing

                # 3. Deduplication check (in-flight)
                if key in self._in_flight_keys:
                    existing = self._in_flight_keys[key]
                    logger.debug(f"[BackgroundIntelligence] {key} coalesced with IN_FLIGHT work")
                    return True, existing

                # 4. Total capacity check
                total_active = len(self._pending_keys) + len(self._in_flight_keys) + self._results_queue.qsize() + len(self._overflow_results)
                if total_active >= (self.max_queue_size + self.max_results_size) or self._queue.full():
                    logger.warning(
                        f"[BackgroundIntelligence] Queue or capacity saturated ({total_active}/{self.max_queue_size + self.max_results_size}). "
                        f"Rejecting work for {key} without blocking coordinator."
                    )
                    return False, None

                # 5. Monotonic sequence assignment
                seq = self._sequences.get(key, 0) + 1
                self._sequences[key] = seq
                self._latest_issued_sequence[key] = seq

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
                if self._shutdown_event.is_set() or item.cancel_event.is_set():
                    item.status = WorkStatus.CANCELLED
                    item.error = "Cancelled due to coordinator shutdown or request"
                    item.event.set()
                    self._queue.task_done()
                    continue
                self._in_flight_keys[item.key] = item
                item.status = WorkStatus.IN_FLIGHT

            # Execute work outside lock
            data = None
            err_msg = None
            was_cancelled = False
            try:
                if self._shutdown_event.is_set() or item.cancel_event.is_set():
                    was_cancelled = True
                    raise RuntimeError("Cancelled before execution")

                # Inject cancel_check if accepted by fn
                call_kwargs = dict(item.kwargs)
                try:
                    import inspect
                    sig = inspect.signature(item.fn)
                    if "cancel_check" in sig.parameters and "cancel_check" not in call_kwargs:
                        call_kwargs["cancel_check"] = lambda: self._shutdown_event.is_set() or item.cancel_event.is_set()
                except Exception:
                    pass

                data = item.fn(*item.args, **call_kwargs)

                if self._shutdown_event.is_set() or item.cancel_event.is_set():
                    was_cancelled = True
                    item.status = WorkStatus.CANCELLED
                    item.error = "Work cancelled during execution"
                else:
                    item.status = WorkStatus.COMPLETED
                    item.result = data
                    self.cooldown_tracker.record_success(item.key)
            except Exception as e:
                err_msg = str(e)
                if was_cancelled or item.cancel_event.is_set() or self._shutdown_event.is_set() or "cancelled" in err_msg.lower():
                    item.status = WorkStatus.CANCELLED
                    item.error = err_msg or "Cancelled"
                else:
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

                # Produce result for coordinator handoff if not cancelled
                if item.status != WorkStatus.CANCELLED:
                    result = WorkResult(
                        key=item.key,
                        sequence=item.sequence,
                        obs_time=item.obs_time,
                        data=data,
                        error=err_msg,
                        completed_time=self.clock_fn(),
                        enqueued_time=item.enqueued_time
                    )
                    try:
                        self._results_queue.put_nowait(result)
                    except queue.Full:
                        # Bounded handoff capacity reached: retain in recoverable overflow store
                        with self._lock:
                            existing = self._overflow_results.get(item.key)
                            if existing is None or result.sequence >= existing.sequence:
                                self._overflow_results[item.key] = result
                            logger.warning(
                                f"[BackgroundIntelligence] Result handoff capacity reached ({self.max_results_size}). "
                                f"Preserving recoverable result for {item.key} sequence={result.sequence} in overflow store."
                            )

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
        with self._lock:
            if self._overflow_results:
                results.extend(self._overflow_results.values())
                self._overflow_results.clear()
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

    def shutdown(self, timeout: float = 2.0) -> bool:
        """
        Gracefully shuts down workers and releases all resources.
        Returns True if all workers cleanly stopped within timeout, False otherwise.
        """
        self._shutdown_event.set()

        # Mark all pending items cancelled and wake waiters
        with self._lock:
            for item in self._pending_keys.values():
                item.cancel_event.set()
                item.status = WorkStatus.CANCELLED
                item.error = "Cancelled due to shutdown"
                item.event.set()
            self._pending_keys.clear()

            # Signal cancellation to in-flight items; DO NOT clear _in_flight_keys.
            # Workers will pop their own keys upon termination.
            for item in self._in_flight_keys.values():
                item.cancel_event.set()
                item.status = WorkStatus.CANCELLED
                item.error = "Cancelled due to shutdown"
                item.event.set()

        # Drain input queue
        while True:
            try:
                item = self._queue.get_nowait()
                item.cancel_event.set()
                item.status = WorkStatus.CANCELLED
                item.error = "Cancelled due to shutdown"
                item.event.set()
                self._queue.task_done()
            except queue.Empty:
                break

        # Join workers
        start = time.monotonic()
        all_joined = True
        for t in self._workers:
            remaining = max(0.01, timeout - (time.monotonic() - start))
            t.join(timeout=remaining)
            if t.is_alive():
                all_joined = False

        with self._lock:
            active_in_flight = len(self._in_flight_keys)

        if not all_joined or active_in_flight > 0:
            alive_workers = [w.name for w in self._workers if w.is_alive()]
            logger.error(
                f"[BackgroundIntelligence] Shutdown incomplete after {timeout}s: "
                f"active workers={alive_workers}, in-flight keys={list(self._in_flight_keys.keys())}"
            )
            return False

        return True


class BackgroundResultApplicator:
    """
    Coordinates application of background intelligence results to coordinator state.
    Enforces monotonic sequence and observation timestamp constraints:
    - Results superseded by older/equal sequence numbers are rejected.
    - Results superseded by newer issued sequences are rejected (Requirement F).
    - Results superseded by older observation timestamps are rejected (instant comparison).
    - Missing/invalid/ambiguous observation timestamps fail closed.
    - Background workers never mutate state directly.
    """
    def __init__(
        self,
        coordinator: Optional[Any] = None,
        latest_issued_provider: Optional[Callable[[WorkKey], int]] = None,
    ):
        self._last_applied_sequence: Dict[WorkKey, int] = {}
        self._last_applied_obs_dt: Dict[WorkKey, datetime] = {}
        self._last_applied_obs_time: Dict[WorkKey, str] = {}
        self._latest_issued_sequence: Dict[WorkKey, int] = {}
        self._key_locks: Dict[WorkKey, threading.RLock] = {}
        self._coordinator = coordinator
        self._latest_issued_provider = latest_issued_provider
        self._lock = threading.RLock()

    def get_key_lock(self, key: WorkKey) -> threading.RLock:
        if self._coordinator is not None and hasattr(self._coordinator, "get_key_lock"):
            return self._coordinator.get_key_lock(key)
        with self._lock:
            if key not in self._key_locks:
                self._key_locks[key] = threading.RLock()
            return self._key_locks[key]

    def record_issued_sequence(self, key: WorkKey, seq: int) -> None:
        with self._lock:
            if seq > self._latest_issued_sequence.get(key, 0):
                self._latest_issued_sequence[key] = seq

    def get_latest_issued_sequence(self, key: WorkKey) -> int:
        with self._lock:
            if self._coordinator is not None and hasattr(self._coordinator, "get_latest_issued_sequence"):
                return self._coordinator.get_latest_issued_sequence(key)
            if callable(self._latest_issued_provider):
                return self._latest_issued_provider(key)
            if isinstance(self._latest_issued_provider, dict):
                return self._latest_issued_provider.get(key, 0)
            return self._latest_issued_sequence.get(key, 0)

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
        scout_category_map: dict,
        latest_issued_provider: Optional[Callable[[WorkKey], int] | Dict[WorkKey, int]] = None,
        require_source_time: bool = False,
        on_publish: Optional[Callable[[WorkResult], None]] = None,
    ) -> List[Tuple[WorkResult, bool, str]]:
        """
        Applies results safely to provided targets under per-key lock.
        Sequence validation and target publication are atomic: a newer sequence
        cannot be issued between sequence validation and result publication.
        When on_publish is provided, it is executed under key_lock before the sequence
        is marked as applied and key_lock is released.
        MARKET_DATA results require a valid source observation timestamp and an explicit
        on_publish callback; they never mutate grounding/scout targets, and the applied
        sequence/timestamp advance only after on_publish succeeds.
        Returns list of (result, applied, reason).
        """
        audit = []
        for r in results:
            key_lock = self.get_key_lock(r.key)
            with key_lock:
                applied = False
                obs_dt = None
                with self._lock:
                    # If worker errored, do not apply data
                    if r.error is not None or r.data is None:
                        audit.append((r, False, f"Worker error: {r.error}"))
                        continue

                    # 1. Validate sequence monotonicity against last applied sequence
                    last_seq = self._last_applied_sequence.get(r.key, -1)
                    if r.sequence <= last_seq:
                        msg = f"Stale sequence: received {r.sequence} <= last applied {last_seq}"
                        logger.warning(f"[BackgroundResultApplicator] {r.key} rejected: {msg}")
                        audit.append((r, False, msg))
                        continue

                    # 2. Validate sequence against latest issued sequence (Atomic with publication)
                    latest_issued = -1
                    if callable(latest_issued_provider):
                        latest_issued = latest_issued_provider(r.key)
                    elif isinstance(latest_issued_provider, dict):
                        latest_issued = latest_issued_provider.get(r.key, -1)
                    else:
                        latest_issued = self.get_latest_issued_sequence(r.key)

                    if latest_issued > 0 and r.sequence < latest_issued:
                        msg = f"Stale sequence: received {r.sequence} is older than latest issued {latest_issued}"
                        logger.warning(f"[BackgroundResultApplicator] {r.key} rejected: {msg}")
                        audit.append((r, False, msg))
                        continue

                    # 3. Validate observation timestamp monotonicity
                    if r.obs_time is not None and str(r.obs_time).strip() != "":
                        try:
                            obs_dt = parse_utc_iso8601_timestamp(r.obs_time)
                        except Exception as e:
                            msg = f"Invalid observation timestamp: {e}"
                            logger.warning(f"[BackgroundResultApplicator] {r.key} rejected fail-closed: {msg}")
                            audit.append((r, False, msg))
                            continue

                        last_obs_dt = self._last_applied_obs_dt.get(r.key)
                        if last_obs_dt is not None and obs_dt < last_obs_dt:
                            msg = f"Stale observation: received {r.obs_time} < last applied {self._last_applied_obs_time.get(r.key)}"
                            logger.warning(f"[BackgroundResultApplicator] {r.key} rejected: {msg}")
                            audit.append((r, False, msg))
                            continue
                    elif require_source_time or r.key.work_type == WorkType.MARKET_DATA.value:
                        msg = "Missing required source observation timestamp"
                        logger.warning(f"[BackgroundResultApplicator] {r.key} rejected fail-closed: {msg}")
                        audit.append((r, False, msg))
                        continue

                    # 4. Apply result based on work type
                    if r.key.work_type == WorkType.GROUNDING.value:
                        grounding_target[r.key.target] = r.data
                        applied = True
                    elif r.key.work_type == WorkType.SCOUT.value:
                        scout_cache_target[r.key.target] = {
                            "tickers": r.data,
                            "cached_at": time.time(),
                            "timestamp": r.obs_time if r.obs_time is not None else None
                        }
                        if isinstance(r.data, list):
                            for t in r.data:
                                if isinstance(t, str):
                                    scout_category_map[t.upper()] = r.key.target
                        applied = True
                    elif r.key.work_type == WorkType.MARKET_DATA.value:
                        # Market-data results are never assigned to shared state here.
                        # The explicit, caller-supplied on_publish callback (executed below on the
                        # coordinating thread under key_lock) is the only permitted publication route.
                        if on_publish is None or not callable(on_publish):
                            msg = "Market-data publication callback required"
                            logger.warning(f"[BackgroundResultApplicator] {r.key} rejected fail-closed: {msg}")
                            audit.append((r, False, msg))
                            continue
                        applied = True
                    else:
                        audit.append((r, False, f"Unknown work type: {r.key.work_type}"))
                        continue

                # 5. Execute publication callback while STILL holding key_lock!
                # This guarantees that sequence validation and actual snapshot publication are atomic:
                # a newer sequence cannot be issued between sequence validation and replacement snapshot publication.
                if applied and on_publish is not None:
                    try:
                        on_publish(r)
                    except Exception as pub_err:
                        logger.error(f"[BackgroundResultApplicator] on_publish failed for {r.key}: {pub_err}")
                        audit.append((r, False, f"Publication failed: {pub_err}"))
                        continue

                with self._lock:
                    if applied:
                        self._last_applied_sequence[r.key] = r.sequence
                        if obs_dt is not None:
                            self._last_applied_obs_dt[r.key] = obs_dt
                            self._last_applied_obs_time[r.key] = r.obs_time
                        audit.append((r, True, "Applied successfully"))

        return audit

    def reset(self) -> None:
        with self._lock:
            self._last_applied_sequence.clear()
            self._last_applied_obs_dt.clear()
            self._last_applied_obs_time.clear()
            self._latest_issued_sequence.clear()
