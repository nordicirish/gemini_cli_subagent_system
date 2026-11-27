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
import inspect
import json
import os
import sys
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

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
        get_persistent_session_status,
        probe_transport,
        sanitize_job_inputs,
        unwrap_exception_message,
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
        get_persistent_session_status,
        probe_transport,
        sanitize_job_inputs,
        unwrap_exception_message,
    )
    from python.persistence import get_persistence_service


MAX_JOB_HISTORY = 50
_WORKERS_DIR = os.path.join(_THIS_DIR, "colab_workers")

STARTUP_PIPELINE_ORDER: Tuple[str, ...] = (
    "vol_surface",
    "sec_nlp",
    "quant_scout",
    "walk_forward",
)

JOB_SPEC: Dict[str, Dict[str, Any]] = {
    "vol_surface": {
        "artifact_type": "vol_surface",
        "default_accelerator": "CPU",
        "worker_filename": "vol_surface_worker.py",
        "label": "Options Flow & Vol Surface",
        "description": "Multi-expiry SVI volatility surface and 2nd-order Greeks (Vanna/Charm) sweep",
    },
    "quant_scout": {
        "artifact_type": "quant_scout",
        "default_accelerator": "CPU",
        "worker_filename": "quant_scout_worker.py",
        "label": "Stock Finder (500+)",
        "description": "Broad 500+ ticker quantitative momentum, PEAD, and portfolio-correlation scout",
    },
    "sec_nlp": {
        "artifact_type": "sec_nlp",
        "default_accelerator": "T4",
        "worker_filename": "sec_nlp_worker.py",
        "label": "SEC Filing Risk Scan",
        "description": "Zero-token GPU NLP risk & catalyst extraction across SEC filings and earnings releases",
    },
    "walk_forward": {
        "artifact_type": "backtest_evidence",
        "default_accelerator": "CPU",
        "worker_filename": "walk_forward_worker.py",
        "label": "Strategy Check (Backtest)",
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
    allowed = {"use_llm", "top_n", "lookback_years", "tickers", "sectors", "max_rsi"}
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
    if "sectors" in raw_options and raw_options["sectors"] is not None:
        if not isinstance(raw_options["sectors"], list):
            raise ValueError("options.sectors must be a list of sector names.")
        sectors_clean = [str(s or "").strip()[:48] for s in raw_options["sectors"] if str(s or "").strip()]
        if len(sectors_clean) > 20:
            raise ValueError("options.sectors cannot exceed 20 entries.")
        clean["sectors"] = sectors_clean
    if "max_rsi" in raw_options and raw_options["max_rsi"] is not None:
        mr = raw_options["max_rsi"]
        if isinstance(mr, bool) or not isinstance(mr, (int, float)) or mr < 20 or mr > 100:
            raise ValueError("options.max_rsi must be a number in [20, 100].")
        clean["max_rsi"] = float(mr)
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
        transport_prober: Optional[Callable[[ColabMCPConfig], Dict[str, Any]]] = None,
    ) -> None:
        self.cache_dir = cache_dir
        self._config_loader = config_loader or ColabMCPConfig.from_env
        self._client_factory = client_factory or (lambda cfg: ColabMCPClient(config=cfg))
        self._worker_loader = worker_loader or _default_worker_loader
        self._service_provider = service_provider or get_persistence_service
        if transport_prober is not None:
            self._transport_prober = transport_prober
        elif client_factory is not None:
            self._transport_prober = lambda cfg: {
                "mode": cfg.mode,
                "ready": cfg.mode != "offline",
                "setup_hint": "Offline mode active; live Colab execution disabled.",
            }
        else:
            self._transport_prober = probe_transport
        self._lock = threading.RLock()
        self._active_by_type: Dict[str, str] = {}
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._pipeline_active: bool = False
        self._pipeline: Dict[str, Any] = {
            "state": "idle",
            "trigger": None,
            "started_at_utc": None,
            "finished_at_utc": None,
            "current_job": None,
            "completed_count": 0,
            "total_count": 0,
            "skipped_fresh_count": 0,
            "failed_count": 0,
            "percent": 0,
            "step_label": "Idle",
            "connect_url": None,
            "jobs": {},
        }

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
            cli_path=base_cfg.cli_path,
            cli_auth=base_cfg.cli_auth,
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
                "label": spec.get("label", norm_type),
                "artifact_type": spec["artifact_type"],
                "mode": cfg.mode,
                "accelerator": accelerator,
                "status": "queued",
                "progress": "Queued for MCP dispatch",
                "progress_pct": 5,
                "connect_url": None,
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
                    progress_pct=20,
                )
                script_source = self._worker_loader(norm_type)
                client = self._client_factory(cfg)
                self._update_job(
                    job_id,
                    progress=f"Executing {spec.get('label', norm_type)} on Colab ({accelerator})",
                    progress_pct=55,
                )
                exec_fn = client.execute_python_script_sync
                exec_sig = inspect.signature(exec_fn)
                extra_kwargs: Dict[str, Any] = {}
                if "status_callback" in exec_sig.parameters:
                    extra_kwargs["status_callback"] = lambda msg: self._update_job(job_id, progress=msg)
                if "connect_url_callback" in exec_sig.parameters:
                    extra_kwargs["connect_url_callback"] = lambda url: self._update_job(job_id, connect_url=url)
                result_payload = exec_fn(
                    script_source,
                    job_inputs,
                    accelerator=accelerator,
                    **extra_kwargs,
                )
                self._update_job(
                    job_id,
                    progress="Validating and saving Colab results",
                    progress_pct=90,
                    connect_url=None,
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
                    progress_pct=100,
                    artifact_summary=summary,
                    error=None,
                )
            except Exception as exc:
                self._update_job(
                    job_id,
                    status="failed",
                    finished_at_utc=_utc_now_iso(),
                    progress="Failed (prior cached artifact preserved)",
                    progress_pct=100,
                    error=redact_secrets(unwrap_exception_message(exc)),
                )
            finally:
                with self._lock:
                    if self._active_by_type.get(norm_type) == job_id:
                        self._active_by_type.pop(norm_type, None)

        queued_snapshot = copy.deepcopy(record)
        t = threading.Thread(target=_worker, name=f"ColabJob-{norm_type}", daemon=daemon)
        t.start()
        return queued_snapshot

    def run_pipeline(
        self,
        *,
        job_types: Optional[List[str]] = None,
        only_stale: bool = True,
        retry_failed_only: bool = False,
        trigger: str = "manual",
        options_by_job: Optional[Dict[str, Dict[str, Any]]] = None,
        service: Optional[Any] = None,
        daemon: bool = True,
    ) -> Dict[str, Any]:
        """Sequentially run Colab quant workers with progress tracking and fresh-cache skipping."""
        raw_types = list(job_types) if job_types else list(STARTUP_PIPELINE_ORDER)
        targets: List[str] = []
        for raw_jt in raw_types:
            norm_jt = str(raw_jt or "").strip().lower()
            if norm_jt not in JOB_SPEC:
                raise ValueError(f"Unknown Colab job_type {raw_jt!r}; valid types: {sorted(JOB_SPEC.keys())}")
            if norm_jt not in targets:
                targets.append(norm_jt)

        cfg = self._config_loader()
        transport = self._transport_prober(cfg)
        if cfg.mode == "offline" or not transport.get("ready"):
            hint = str(transport.get("setup_hint") or "Colab MCP transport is not configured.")
            with self._lock:
                self._pipeline = {
                    "state": "setup_required",
                    "trigger": trigger,
                    "started_at_utc": _utc_now_iso(),
                    "finished_at_utc": _utc_now_iso(),
                    "current_job": None,
                    "completed_count": 0,
                    "total_count": len(targets),
                    "skipped_fresh_count": 0,
                    "failed_count": 0,
                    "percent": 0,
                    "step_label": hint,
                    "jobs": {
                        jt: {
                            "status": "setup_required",
                            "label": JOB_SPEC[jt]["label"],
                            "job_id": None,
                            "error": hint,
                            "progress_pct": 0,
                        }
                        for jt in targets
                    },
                }
                return copy.deepcopy(self._pipeline)

        with self._lock:
            if self._pipeline_active:
                return copy.deepcopy(self._pipeline)
            prev_jobs = copy.deepcopy(self._pipeline.get("jobs") or {})

        artifacts_status = get_all_colab_artifacts_status(cache_dir=self.cache_dir).get("artifacts", {})
        jobs_map: Dict[str, Dict[str, Any]] = {}
        to_run: List[str] = []
        skipped_fresh = 0

        failed_targets = (
            [jt for jt in targets if (prev_jobs.get(jt) or {}).get("status") in {"failed", "setup_required"}]
            if retry_failed_only
            else []
        )

        if retry_failed_only and failed_targets:
            for jt in targets:
                if jt in failed_targets:
                    to_run.append(jt)
                    jobs_map[jt] = {
                        "status": "pending",
                        "label": JOB_SPEC[jt]["label"],
                        "job_id": None,
                        "error": None,
                        "progress_pct": 0,
                    }
                else:
                    prev_st = str((prev_jobs.get(jt) or {}).get("status") or "skipped_fresh")
                    kept_status = prev_st if prev_st in {"succeeded", "skipped_fresh"} else "skipped_fresh"
                    skipped_fresh += 1
                    jobs_map[jt] = {
                        "status": kept_status,
                        "label": JOB_SPEC[jt]["label"],
                        "job_id": (prev_jobs.get(jt) or {}).get("job_id"),
                        "error": None,
                        "progress_pct": 100,
                    }
        else:
            eff_only_stale = True if retry_failed_only else bool(only_stale)
            for jt in targets:
                art_key = JOB_SPEC[jt]["artifact_type"]
                art_info = artifacts_status.get(art_key, {}) if isinstance(artifacts_status, dict) else {}
                is_fresh = bool(art_info.get("loaded")) and not bool((art_info.get("freshness") or {}).get("stale", True))
                if eff_only_stale and is_fresh:
                    skipped_fresh += 1
                    jobs_map[jt] = {
                        "status": "skipped_fresh",
                        "label": JOB_SPEC[jt]["label"],
                        "job_id": None,
                        "error": None,
                        "progress_pct": 100,
                    }
                else:
                    to_run.append(jt)
                    jobs_map[jt] = {
                        "status": "pending",
                        "label": JOB_SPEC[jt]["label"],
                        "job_id": None,
                        "error": None,
                        "progress_pct": 0,
                    }

        with self._lock:
            if self._pipeline_active:
                return copy.deepcopy(self._pipeline)
            total_count = len(targets)
            if not to_run:
                self._pipeline = {
                    "state": "succeeded",
                    "trigger": trigger,
                    "started_at_utc": _utc_now_iso(),
                    "finished_at_utc": _utc_now_iso(),
                    "current_job": None,
                    "completed_count": total_count,
                    "total_count": total_count,
                    "skipped_fresh_count": skipped_fresh,
                    "failed_count": 0,
                    "percent": 100,
                    "step_label": "All cloud insights are up to date",
                    "jobs": jobs_map,
                }
                return copy.deepcopy(self._pipeline)

            self._pipeline_active = True
            self._pipeline = {
                "state": "running",
                "trigger": trigger,
                "started_at_utc": _utc_now_iso(),
                "finished_at_utc": None,
                "current_job": to_run[0],
                "completed_count": skipped_fresh,
                "total_count": total_count,
                "skipped_fresh_count": skipped_fresh,
                "failed_count": 0,
                "percent": round((skipped_fresh / max(1, total_count)) * 100),
                "step_label": f"Starting {JOB_SPEC[to_run[0]]['label']}...",
                "error_summary": None,
                "jobs": jobs_map,
            }
            snapshot = copy.deepcopy(self._pipeline)

        opts_lookup = options_by_job or {}

        def _recompute_percent_locked() -> int:
            total = max(1, len(targets))
            acc = 0.0
            for t_name in targets:
                entry = self._pipeline["jobs"].get(t_name, {})
                acc += max(0.0, min(100.0, float(entry.get("progress_pct", 0))))
            return max(0, min(100, int(round(acc / total))))

        def _pipeline_worker() -> None:
            failed_cnt = 0
            completed_cnt = skipped_fresh
            first_error: Optional[str] = None
            try:
                client = self._client_factory(cfg)
                use_batch_session = hasattr(client, "execute_scripts_batch_sync")
                if use_batch_session:
                    batch_items: List[tuple] = []
                    for jt in to_run:
                        job_opts = dict(opts_lookup.get(jt) or {})
                        clean_opts = _sanitize_options(job_opts)
                        acc = "L4" if (jt == "sec_nlp" and clean_opts.get("use_llm")) else JOB_SPEC[jt]["default_accelerator"]
                        batch_items.append((
                            jt,
                            self._worker_loader(jt),
                            self._build_job_inputs(jt, clean_opts, service),
                            acc,
                        ))

                    def _on_step(step_jt: str, phase: str, msg: str) -> None:
                        with self._lock:
                            step_idx = targets.index(step_jt) + 1 if step_jt in targets else 1
                            lbl = JOB_SPEC.get(step_jt, {}).get("label", step_jt)
                            self._pipeline["current_job"] = step_jt
                            if phase == "handshake":
                                self._pipeline["step_label"] = f"Step {step_idx}/{len(targets)}: {lbl} — {msg}"
                                self._pipeline["jobs"][step_jt]["status"] = "running"
                                self._pipeline["jobs"][step_jt]["progress_pct"] = 25
                            elif phase == "running":
                                self._pipeline["step_label"] = f"Step {step_idx}/{len(targets)}: Running {lbl}..."
                                self._pipeline["jobs"][step_jt]["status"] = "running"
                                self._pipeline["jobs"][step_jt]["progress_pct"] = 45
                            elif phase == "succeeded":
                                self._pipeline["jobs"][step_jt]["status"] = "succeeded"
                                self._pipeline["jobs"][step_jt]["progress_pct"] = 100
                            elif phase == "failed":
                                self._pipeline["jobs"][step_jt]["status"] = "failed"
                                self._pipeline["jobs"][step_jt]["progress_pct"] = 100
                                self._pipeline["jobs"][step_jt]["error"] = redact_secrets(msg)
                            self._pipeline["percent"] = _recompute_percent_locked()

                    def _on_connect_url(url: Optional[str]) -> None:
                        with self._lock:
                            self._pipeline["connect_url"] = url

                    batch_fn = client.execute_scripts_batch_sync
                    batch_kwargs: Dict[str, Any] = {"progress_callback": _on_step}
                    if "connect_url_callback" in inspect.signature(batch_fn).parameters:
                        batch_kwargs["connect_url_callback"] = _on_connect_url
                    batch_out = batch_fn(batch_items, **batch_kwargs)
                    for jt in to_run:
                        res_item = batch_out.get(jt) or {"ok": False, "error": "No result returned"}
                        if res_item.get("ok"):
                            try:
                                import_colab_artifact(
                                    JOB_SPEC[jt]["artifact_type"],
                                    res_item["payload"],
                                    cache_dir=self.cache_dir,
                                )
                                completed_cnt += 1
                                with self._lock:
                                    self._pipeline["jobs"][jt].update({"status": "succeeded", "progress_pct": 100, "error": None})
                            except Exception as exc:
                                err_txt = redact_secrets(unwrap_exception_message(exc))
                                if first_error is None:
                                    first_error = err_txt
                                failed_cnt += 1
                                completed_cnt += 1
                                with self._lock:
                                    self._pipeline["jobs"][jt].update({"status": "failed", "progress_pct": 100, "error": err_txt})
                        else:
                            err_txt = redact_secrets(str(res_item.get("error") or f"{jt} failed"))
                            if first_error is None:
                                first_error = err_txt
                            failed_cnt += 1
                            completed_cnt += 1
                            with self._lock:
                                self._pipeline["jobs"][jt].update({"status": "failed", "progress_pct": 100, "error": err_txt})
                        with self._lock:
                            self._pipeline["failed_count"] = failed_cnt
                            self._pipeline["completed_count"] = completed_cnt
                            self._pipeline["percent"] = _recompute_percent_locked()
                else:
                    for step_idx, jt in enumerate(targets, start=1):
                        if jt not in to_run:
                            continue
                        label = JOB_SPEC[jt]["label"]
                        with self._lock:
                            self._pipeline["current_job"] = jt
                            self._pipeline["step_label"] = f"Step {step_idx}/{len(targets)}: Running {label}..."
                            self._pipeline["jobs"][jt]["status"] = "running"
                            self._pipeline["jobs"][jt]["progress_pct"] = 5

                        job_opts = dict(opts_lookup.get(jt) or {})
                        job_id: Optional[str] = None
                        try:
                            sub = self.submit_job(
                                jt,
                                options=job_opts,
                                service=service,
                                daemon=daemon,
                            )
                            job_id = sub["job_id"]
                        except ColabJobConflictError:
                            with self._lock:
                                job_id = self._active_by_type.get(jt)
                        except Exception as exc:
                            err_txt = redact_secrets(unwrap_exception_message(exc))
                            if first_error is None:
                                first_error = err_txt
                            failed_cnt += 1
                            completed_cnt += 1
                            with self._lock:
                                self._pipeline["jobs"][jt].update({
                                    "status": "failed",
                                    "progress_pct": 100,
                                    "error": err_txt,
                                })
                                self._pipeline["failed_count"] = failed_cnt
                                self._pipeline["completed_count"] = completed_cnt
                                self._pipeline["percent"] = _recompute_percent_locked()
                            continue

                        if not job_id:
                            continue
                        with self._lock:
                            self._pipeline["jobs"][jt]["job_id"] = job_id

                        # Poll until single-flight job completes
                        deadline = time.monotonic() + float(cfg.timeout_sec) + 30.0
                        while time.monotonic() < deadline:
                            rec = self.get_job(job_id)
                            if not rec:
                                break
                            j_status = rec.get("status")
                            j_pct = int(rec.get("progress_pct", 50 if j_status == "running" else 100))
                            with self._lock:
                                self._pipeline["jobs"][jt]["progress_pct"] = j_pct
                                if rec.get("progress"):
                                    self._pipeline["step_label"] = f"Step {step_idx}/{len(targets)}: {rec['progress']}"
                                self._pipeline["percent"] = _recompute_percent_locked()
                            if j_status in {"succeeded", "failed"}:
                                completed_cnt += 1
                                if j_status == "failed":
                                    failed_cnt += 1
                                    if first_error is None and rec.get("error"):
                                        first_error = str(rec.get("error"))
                                with self._lock:
                                    self._pipeline["jobs"][jt].update({
                                        "status": j_status,
                                        "progress_pct": 100,
                                        "error": rec.get("error"),
                                    })
                                    self._pipeline["failed_count"] = failed_cnt
                                    self._pipeline["completed_count"] = completed_cnt
                                    self._pipeline["percent"] = _recompute_percent_locked()
                                break
                            time.sleep(0.1)

                with self._lock:
                    if failed_cnt == 0:
                        final_state = "succeeded"
                        final_label = f"All {len(targets)} cloud research steps complete"
                    elif failed_cnt < len(targets):
                        final_state = "partial"
                        final_label = f"Completed with {failed_cnt} issue(s) across {len(targets)} steps"
                    else:
                        final_state = "failed"
                        final_label = f"Cloud research finished with {failed_cnt} failure(s)"
                    self._pipeline.update({
                        "state": final_state,
                        "finished_at_utc": _utc_now_iso(),
                        "current_job": None,
                        "completed_count": len(targets),
                        "failed_count": failed_cnt,
                        "percent": 100,
                        "step_label": final_label,
                        "error_summary": first_error,
                        "connect_url": None,
                    })
            finally:
                with self._lock:
                    self._pipeline_active = False

        pt = threading.Thread(target=_pipeline_worker, name="ColabPipelineWorker", daemon=daemon)
        pt.start()
        return snapshot

    def maybe_start_startup_pipeline(self) -> Dict[str, Any]:
        """Trigger automatic stale-only Colab research on startup only when opted in (`COLAB_AUTO_RUN_ON_STARTUP=1`)."""
        raw_flag = (os.environ.get("COLAB_AUTO_RUN_ON_STARTUP") or "0").strip().lower()
        if raw_flag not in {"1", "true", "yes", "on"}:
            with self._lock:
                self._pipeline = {
                    "state": "disabled",
                    "trigger": "startup",
                    "started_at_utc": _utc_now_iso(),
                    "finished_at_utc": _utc_now_iso(),
                    "current_job": None,
                    "completed_count": 0,
                    "total_count": 0,
                    "skipped_fresh_count": 0,
                    "failed_count": 0,
                    "percent": 0,
                    "step_label": "Cloud research runs when you click ▶ Refresh All Cloud Insights (set COLAB_AUTO_RUN_ON_STARTUP=1 to auto-run on startup)",
                    "connect_url": None,
                    "jobs": {},
                }
                return copy.deepcopy(self._pipeline)
        return self.run_pipeline(only_stale=True, trigger="startup", daemon=True)

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
            pipeline_snapshot = copy.deepcopy(self._pipeline)
        artifacts_status = get_all_colab_artifacts_status(cache_dir=self.cache_dir)
        try:
            session_status = get_persistent_session_status(cfg)
        except Exception:
            session_status = {"persistent": False, "alive": False, "browser_linked": False}
        return {
            "transport": self._transport_prober(cfg),
            "session": session_status,
            "job_types": {
                k: {
                    "label": v.get("label", k),
                    "artifact_type": v["artifact_type"],
                    "default_accelerator": v["default_accelerator"],
                    "description": v["description"],
                }
                for k, v in JOB_SPEC.items()
            },
            "pipeline": pipeline_snapshot,
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
        print("Usage: python -m colab_jobs [status|run <job_type> [--mode cli|stdio|http] [--use-llm]]")
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
            print("--mode requires cli, stdio, or http")
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
