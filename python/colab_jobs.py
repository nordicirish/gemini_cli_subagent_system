"""
Single-Flight Async Google Colab MCP Job Runner & CLI (Stage M1).

Manages on-demand execution of heavy batch quant jobs (`vol_surface`, `quant_scout`,
`sec_nlp`, `walk_forward`) via `ColabMCPClient`, enforces single-flight locks per job type,
and persists only fail-closed Schema v2.0 validated outputs into `cache/`.
"""

from __future__ import annotations

from collections import OrderedDict
import copy
from datetime import datetime, timezone
import json
import os
import sys
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(1, _REPO_ROOT)
if _THIS_DIR not in sys.path:
    sys.path.insert(1, _THIS_DIR)

try:
    from audit_logger import redact_secrets
    from colab_calibration import (
        DEFAULT_CACHE_DIR,
        export_colab_bundle,
        get_all_colab_artifacts_status,
        import_colab_artifact,
    )
    from colab_mcp_client import (
        ColabMCPClient,
        ColabMCPConfig,
        probe_transport,
        sanitize_job_inputs,
    )
    from persistence import get_persistence_service
except ImportError:
    from python.audit_logger import redact_secrets
    from python.colab_calibration import (
        DEFAULT_CACHE_DIR,
        export_colab_bundle,
        get_all_colab_artifacts_status,
        import_colab_artifact,
    )
    from python.colab_mcp_client import (
        ColabMCPClient,
        ColabMCPConfig,
        probe_transport,
        sanitize_job_inputs,
    )
    from python.persistence import get_persistence_service


MAX_JOB_HISTORY = 50
_WORKERS_DIR = os.path.join(_THIS_DIR, "colab_workers")

JOB_SPEC: Dict[str, Dict[str, Any]] = {
    "vol_surface": {
        "artifact_type": "vol_surface",
        "default_accelerator": "CPU",
        "worker_filename": "vol_surface_worker.py",
        "description": "Multi-expiry SVI volatility surface and 2nd-order Greeks (Vanna/Charm) sweep",
    },
    "quant_scout": {
        "artifact_type": "quant_scout",
        "default_accelerator": "CPU",
        "worker_filename": "quant_scout_worker.py",
        "description": "Broad 500+ ticker quantitative momentum, PEAD, and portfolio-correlation scout",
    },
    "sec_nlp": {
        "artifact_type": "sec_nlp",
        "default_accelerator": "T4",
        "worker_filename": "sec_nlp_worker.py",
        "description": "Zero-token GPU NLP risk & catalyst extraction across SEC filings and earnings releases",
    },
    "walk_forward": {
        "artifact_type": "backtest_evidence",
        "default_accelerator": "CPU",
        "worker_filename": "walk_forward_worker.py",
        "description": "Multi-year walk-forward rule parameter sweep producing read-only Protocol 06 Evidence Pack",
    },
}

JOB_TYPE_TO_ARTIFACT: Dict[str, str] = {
    k: v["artifact_type"] for k, v in JOB_SPEC.items()
}


class ColabJobConflictError(RuntimeError):
    """Raised when a single-flight Colab job of the same type is already active."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_worker_loader(job_type: str) -> str:
    spec = JOB_SPEC[job_type]
    worker_path = os.path.join(_WORKERS_DIR, spec["worker_filename"])
    if not os.path.isfile(worker_path):
        raise FileNotFoundError(
            f"Worker script not yet installed for {job_type!r} ({spec['worker_filename']}; scheduled in Stage M2-M4)."
        )
    with open(worker_path, "r", encoding="utf-8") as f:
        return f.read()


def _sanitize_options(raw_options: Any) -> Dict[str, Any]:
    if raw_options is None:
        return {}
    if not isinstance(raw_options, dict):
        raise ValueError("options must be a JSON object.")
    allowed = {"use_llm", "top_n", "lookback_years", "tickers"}
    unknown = set(raw_options.keys()) - allowed
    if unknown:
        raise ValueError(f"Unknown Colab job option(s): {sorted(unknown)}")
    clean: Dict[str, Any] = {}
    if "use_llm" in raw_options:
        if not isinstance(raw_options["use_llm"], bool):
            raise ValueError("options.use_llm must be a boolean.")
        clean["use_llm"] = bool(raw_options["use_llm"])
    if "top_n" in raw_options:
        tn = raw_options["top_n"]
        if isinstance(tn, bool) or not isinstance(tn, int) or tn < 1 or tn > 100:
            raise ValueError("options.top_n must be an integer in [1, 100].")
        clean["top_n"] = int(tn)
    if "lookback_years" in raw_options:
        ly = raw_options["lookback_years"]
        if isinstance(ly, bool) or not isinstance(ly, int) or ly < 1 or ly > 10:
            raise ValueError("options.lookback_years must be an integer in [1, 10].")
        clean["lookback_years"] = int(ly)
    if "tickers" in raw_options and raw_options["tickers"] is not None:
        if not isinstance(raw_options["tickers"], list):
            raise ValueError("options.tickers must be a list of ticker symbols.")
        clean["tickers"] = [str(t or "").strip().upper() for t in raw_options["tickers"] if str(t or "").strip()]
    return clean


class ColabJobManager:
    """Thread-safe single-flight manager dispatching Colab quant jobs via ColabMCPClient."""

    def __init__(
        self,
        *,
        cache_dir: str = DEFAULT_CACHE_DIR,
        config_loader: Optional[Callable[[], ColabMCPConfig]] = None,
        client_factory: Optional[Callable[[ColabMCPConfig], ColabMCPClient]] = None,
        worker_loader: Optional[Callable[[str], str]] = None,
        service_provider: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.cache_dir = cache_dir
        self._config_loader = config_loader or ColabMCPConfig.from_env
        self._client_factory = client_factory or (lambda cfg: ColabMCPClient(config=cfg))
        self._worker_loader = worker_loader or _default_worker_loader
        self._service_provider = service_provider or get_persistence_service
        self._lock = threading.RLock()
        self._active_by_type: Dict[str, str] = {}
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def _build_job_inputs(self, job_type: str, options: Dict[str, Any], service: Optional[Any]) -> Dict[str, Any]:
        eff_service = service if service is not None else self._service_provider()
        bundle = export_colab_bundle(service=eff_service)
        symbols = list(bundle.get("symbols", []))
        if options.get("tickers"):
            symbols = list(options["tickers"])
        payload: Dict[str, Any] = {
            "job_type": job_type,
            "artifact_type": JOB_SPEC[job_type]["artifact_type"],
            "symbols": symbols,
            "portfolio_weights_pct": bundle.get("portfolio_weights_pct", {}),
            "options": options,
            "requested_at_utc": _utc_now_iso(),
        }
        if job_type == "walk_forward":
            payload["decision_log_summary"] = bundle.get("decision_log_summary", [])
        return sanitize_job_inputs(payload)

    def _update_job(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is not None:
                rec.update(fields)

    def submit_job(
        self,
        job_type: str,
        *,
        options: Optional[Dict[str, Any]] = None,
        mode: Optional[str] = None,
        service: Optional[Any] = None,
        daemon: bool = True,
    ) -> Dict[str, Any]:
        norm_type = str(job_type or "").strip().lower()
        if norm_type not in JOB_SPEC:
            raise ValueError(f"Unknown Colab job_type {job_type!r}; valid types: {sorted(JOB_SPEC.keys())}")

        clean_options = _sanitize_options(options)
        base_cfg = self._config_loader()
        eff_mode = str(mode).strip().lower() if mode else base_cfg.mode
        cfg = ColabMCPConfig(
            mode=eff_mode,
            stdio_command=base_cfg.stdio_command,
            stdio_args=base_cfg.stdio_args,
            http_url=base_cfg.http_url,
            http_bearer_token=base_cfg.http_bearer_token,
            timeout_sec=base_cfg.timeout_sec,
            allow_loopback_http=base_cfg.allow_loopback_http,
        )
        if cfg.mode == "offline":
            raise ValueError(
                "Cannot submit live MCP job while mode='offline'; upload offline JSON via /api/colab/artifacts/{artifact_type}/import."
            )

        spec = JOB_SPEC[norm_type]
        accelerator = spec["default_accelerator"]
        if norm_type == "sec_nlp" and clean_options.get("use_llm"):
            accelerator = "L4"

        job_inputs = self._build_job_inputs(norm_type, clean_options, service)

        with self._lock:
            existing_id = self._active_by_type.get(norm_type)
            if existing_id:
                raise ColabJobConflictError(
                    f"Colab job type {norm_type!r} is already running (job_id={existing_id})."
                )
            job_id = f"colab-{norm_type}-{uuid.uuid4().hex[:10]}"
            record: Dict[str, Any] = {
                "job_id": job_id,
                "job_type": norm_type,
                "artifact_type": spec["artifact_type"],
                "mode": cfg.mode,
                "accelerator": accelerator,
                "status": "queued",
                "progress": "Queued for MCP dispatch",
                "created_at_utc": _utc_now_iso(),
                "started_at_utc": None,
                "finished_at_utc": None,
                "symbol_count": len(job_inputs.get("symbols", [])),
                "artifact_summary": None,
                "error": None,
            }
            self._jobs[job_id] = record
            self._active_by_type[norm_type] = job_id
            while len(self._jobs) > MAX_JOB_HISTORY:
                oldest_id, _ = next(iter(self._jobs.items()))
                if oldest_id in self._active_by_type.values():
                    break
                self._jobs.popitem(last=False)

        def _worker() -> None:
            try:
                self._update_job(
                    job_id,
                    status="running",
                    started_at_utc=_utc_now_iso(),
                    progress="Loading worker script and connecting to Colab MCP",
                )
                script_source = self._worker_loader(norm_type)
                client = self._client_factory(cfg)
                self._update_job(job_id, progress=f"Executing {norm_type} on Colab ({accelerator})")
                result_payload = client.execute_python_script_sync(
                    script_source,
                    job_inputs,
                    accelerator=accelerator,
                )
                summary = import_colab_artifact(
                    spec["artifact_type"],
                    result_payload,
                    cache_dir=self.cache_dir,
                )
                self._update_job(
                    job_id,
                    status="succeeded",
                    finished_at_utc=_utc_now_iso(),
                    progress="Completed and validated into cache",
                    artifact_summary=summary,
                    error=None,
                )
            except Exception as exc:
                self._update_job(
                    job_id,
                    status="failed",
                    finished_at_utc=_utc_now_iso(),
                    progress="Failed (prior cached artifact preserved)",
                    error=redact_secrets(str(exc)),
                )
            finally:
                with self._lock:
                    if self._active_by_type.get(norm_type) == job_id:
                        self._active_by_type.pop(norm_type, None)

        queued_snapshot = copy.deepcopy(record)
        t = threading.Thread(target=_worker, name=f"ColabJob-{norm_type}", daemon=daemon)
        t.start()
        return queued_snapshot

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            rec = self._jobs.get(str(job_id or "").strip())
            return copy.deepcopy(rec) if rec is not None else None

    def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        max_n = max(1, min(MAX_JOB_HISTORY, int(limit or 20)))
        with self._lock:
            items = list(reversed(self._jobs.values()))[:max_n]
            return copy.deepcopy(items)

    def get_status(self) -> Dict[str, Any]:
        cfg = self._config_loader()
        with self._lock:
            active_jobs = {k: v for k, v in self._active_by_type.items()}
            recent_jobs = self.list_jobs(limit=10)
        artifacts_status = get_all_colab_artifacts_status(cache_dir=self.cache_dir)
        return {
            "transport": probe_transport(cfg),
            "job_types": {
                k: {
                    "artifact_type": v["artifact_type"],
                    "default_accelerator": v["default_accelerator"],
                    "description": v["description"],
                }
                for k, v in JOB_SPEC.items()
            },
            "active_jobs": active_jobs,
            "recent_jobs": recent_jobs,
            "artifacts": artifacts_status["artifacts"],
            "calibration_v1": artifacts_status["calibration_v1"],
        }


_DEFAULT_MANAGER_LOCK = threading.Lock()
_DEFAULT_MANAGER: Optional[ColabJobManager] = None


def get_colab_job_manager() -> ColabJobManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_MANAGER_LOCK:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = ColabJobManager()
        return _DEFAULT_MANAGER


def _cli(argv: List[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"status", "run"}:
        print("Usage: python -m colab_jobs [status|run <job_type> [--mode stdio|http] [--use-llm]]")
        return 1
    mgr = get_colab_job_manager()
    if argv[1] == "status":
        print(json.dumps(mgr.get_status(), indent=2))
        return 0
    if len(argv) < 3:
        print("Specify a job_type: vol_surface, quant_scout, sec_nlp, walk_forward")
        return 1
    job_type = argv[2]
    mode: Optional[str] = None
    if "--mode" in argv:
        idx = argv.index("--mode")
        if idx + 1 >= len(argv):
            print("--mode requires stdio or http")
            return 1
        mode = argv[idx + 1]
    options: Dict[str, Any] = {}
    if "--use-llm" in argv:
        options["use_llm"] = True
    try:
        rec = mgr.submit_job(job_type, options=options, mode=mode, daemon=False)
        # Non-daemon thread may still be finishing; fetch latest record
        import time
        for _ in range(6000):
            latest = mgr.get_job(rec["job_id"])
            if latest and latest["status"] in {"succeeded", "failed"}:
                print(json.dumps(latest, indent=2))
                return 0 if latest["status"] == "succeeded" else 1
            time.sleep(0.1)
        print(json.dumps(mgr.get_job(rec["job_id"]), indent=2))
        return 1
    except Exception as exc:
        print(f"Error: {redact_secrets(str(exc))}")
        return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
