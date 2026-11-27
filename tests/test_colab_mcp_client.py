"""
Stage M1 Unit & Integration Tests for Google Colab MCP Bridge, Job Runner & Schema v2.0 Contracts:
- Fail-closed Schema v2.0 validation & TTL freshness for vol_surface, quant_scout, sec_nlp, backtest_evidence
- Zero-secret egress sanitizer blocking cash/shares/WAC/secrets
- ColabMCPClient SSRF URL allowlist, marker extraction, stdio/http adapter execution (mocked MCP sessions)
- ColabJobManager single-flight enforcement, async lifecycle, error isolation preserving prior cache
- FastAPI /api/colab/* route contract (202/409/400/422/404/200)
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import colab_calibration
import colab_jobs
import colab_mcp_client
import web_server


def _utc_str(hours_ago: float = 0.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sample_vol_surface(hours_ago: float = 0.0) -> dict:
    return {
        "schema_version": "2.0",
        "generated_at_utc": _utc_str(hours_ago),
        "source": "google_colab_mcp",
        "tickers": {
            "NVDA": {
                "atm_iv_30d": 48.5,
                "svi_skew_25d": -4.2,
                "term_structure_slope": 1.3,
                "fit_rmse": 0.014,
                "gamma_flip_multi_expiry": 132.5,
                "net_gex_multi_expiry": 420.1,
                "vanna_exposure": 88.4,
                "charm_decay_bias": -12.1,
                "dealer_flow_regime": "VANNA_TAILWIND",
                "expiries_analyzed": 5,
            }
        },
    }


def _sample_quant_scout(hours_ago: float = 0.0) -> dict:
    return {
        "schema_version": "2.0",
        "generated_at_utc": _utc_str(hours_ago),
        "universe_size": 520,
        "candidates": [
            {
                "ticker": "PLTR",
                "composite_score": 84.2,
                "rs_1m_pct": 12.4,
                "rs_3m_pct": 38.1,
                "rs_6m_pct": 71.0,
                "corr_to_portfolio": 0.28,
                "vol_regime": "NORMAL",
                "cluster_label": "AI_DEFENSE_SW",
                "pead_score": 65.0,
                "adv_usd_m": 1850.5,
                "rationale": "High 3m RS with low portfolio correlation.",
            }
        ],
    }


def _sample_sec_nlp(hours_ago: float = 0.0) -> dict:
    return {
        "schema_version": "2.0",
        "generated_at_utc": _utc_str(hours_ago),
        "model_used": "ProsusAI/finbert",
        "tickers": {
            "UMAC": {
                "dilution_risk_score": 18.5,
                "catalyst_sentiment": 0.62,
                "offering_overhang_flag": False,
                "going_concern_flag": False,
                "earnings_tone_delta": 0.24,
                "filings_analyzed": 4,
                "shelf_remaining_usd_m": 25.0,
                "warrant_overhang_pct": 3.2,
                "evidence_snippets": ["8-K Ex-99.1 notes record backlog growth and positive cash runway."],
            }
        },
    }


def _sample_backtest_evidence(hours_ago: float = 0.0) -> dict:
    return {
        "schema_version": "2.0",
        "generated_at_utc": _utc_str(hours_ago),
        "protocol_06_read_only": True,
        "folds": 8,
        "universe_count": 42,
        "friction_hurdle_pct": 0.85,
        "baseline_metrics": {
            "sharpe_ratio": 1.18,
            "sortino_ratio": 1.62,
            "max_drawdown_pct": -15.4,
            "hit_rate_pct": 54.0,
            "profit_factor": 1.48,
        },
        "candidate_metrics": {
            "sharpe_ratio": 1.41,
            "sortino_ratio": 2.05,
            "max_drawdown_pct": -11.9,
            "hit_rate_pct": 58.5,
            "profit_factor": 1.76,
        },
        "stability_score": 0.81,
        "proposed_rule_deltas": [
            {
                "rule_id": "ENH_104",
                "parameter": "trailing_stop_vwap_ext_pct",
                "current_value": 2.0,
                "proposed_value": 2.25,
                "rationale": "Improves OOS Sharpe across 8 walk-forward folds after 0.85% hurdle.",
            }
        ],
    }


class _FakeTool:
    def __init__(self, name: str, properties: dict):
        self.name = name
        self.inputSchema = {"type": "object", "properties": properties}


class _FakeResult:
    def __init__(self, text: str, is_error: bool = False):
        self.isError = is_error
        self.content = [type("_TextItem", (), {"text": text})()]
        self.structuredContent = None


class _FakeMCPSession:
    def __init__(self, tools, handler):
        self._tools = tools
        self._handler = handler
        self.calls = []

    async def initialize(self):
        return None

    async def list_tools(self):
        return type("_ListTools", (), {"tools": self._tools})()

    async def call_tool(self, name: str, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        return self._handler(name, dict(arguments or {}))


class TestSchemaV2Contracts(unittest.TestCase):
    def test_valid_v2_artifacts_import_load_and_ttl(self):
        samples = {
            "vol_surface": _sample_vol_surface(hours_ago=2.0),
            "quant_scout": _sample_quant_scout(hours_ago=10.0),
            "sec_nlp": _sample_sec_nlp(hours_ago=20.0),
            "backtest_evidence": _sample_backtest_evidence(hours_ago=5.0),
        }
        with tempfile.TemporaryDirectory() as td:
            for art_type, payload in samples.items():
                summary = colab_calibration.import_colab_artifact(art_type, payload, cache_dir=td)
                self.assertEqual(summary["status"], "success")
                self.assertEqual(summary["schema_version"], "2.0")
                loaded = colab_calibration.load_colab_artifact(art_type, cache_dir=td)
                self.assertIsNotNone(loaded)
                status = colab_calibration.get_artifact_status(art_type, cache_dir=td)
                self.assertTrue(status["loaded"])
                self.assertTrue(status["freshness"]["fresh"])

            stale_payload = _sample_vol_surface(hours_ago=48.0)
            colab_calibration.import_colab_artifact("vol_surface", stale_payload, cache_dir=td)
            stale_status = colab_calibration.get_artifact_status("vol_surface", cache_dir=td)
            self.assertTrue(stale_status["loaded"])
            self.assertTrue(stale_status["freshness"]["stale"])

    def test_v2_fail_closed_validation(self):
        bad_ver = _sample_vol_surface()
        bad_ver["schema_version"] = "1.0"
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_artifact("vol_surface", bad_ver)

        bad_nan = _sample_vol_surface()
        bad_nan["tickers"]["NVDA"]["vanna_exposure"] = float("nan")
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_artifact("vol_surface", bad_nan)

        bad_unknown = _sample_quant_scout()
        bad_unknown["candidates"][0]["unexpected"] = 1
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_artifact("quant_scout", bad_unknown)

        bad_bool = _sample_sec_nlp()
        bad_bool["tickers"]["UMAC"]["dilution_risk_score"] = True
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_artifact("sec_nlp", bad_bool)

        bad_p06 = _sample_backtest_evidence()
        bad_p06["protocol_06_read_only"] = False
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_artifact("backtest_evidence", bad_p06)

        with tempfile.TemporaryDirectory() as td:
            corrupt_path = colab_calibration.get_artifact_cache_path("sec_nlp", cache_dir=td)
            with open(corrupt_path, "w", encoding="utf-8") as f:
                f.write("{corrupt")
            self.assertIsNone(colab_calibration.load_colab_artifact("sec_nlp", cache_dir=td))


class TestColabMCPClient(unittest.TestCase):
    def test_sanitize_job_inputs_blocks_secrets_and_financial_balances(self):
        clean = colab_mcp_client.sanitize_job_inputs(
            {"symbols": ["nvda", "umac", "NVDA"], "portfolio_weights_pct": {"nvda": 60, "umac": 40}}
        )
        self.assertEqual(clean["symbols"], ["NVDA", "UMAC"])
        self.assertEqual(clean["portfolio_weights_pct"], {"NVDA": 60.0, "UMAC": 40.0})

        for bad_payload in (
            {"unallocated_cash_eur": 5000.0},
            {"nested": {"shares": 100}},
            {"nested": {"wac": 12.5}},
            {"finnhub_api_key": "abc"},
            {"oauth_token": "xyz"},
        ):
            with self.assertRaises(ValueError):
                colab_mcp_client.sanitize_job_inputs(bad_payload)

    def test_url_allowlist_and_marker_parser(self):
        cfg_ok = colab_mcp_client.ColabMCPConfig(mode="http", http_url="https://colaboratory.googleapis.com/mcp")
        self.assertEqual(cfg_ok.mode, "http")

        with self.assertRaises(ValueError):
            colab_mcp_client.ColabMCPConfig(mode="http", http_url="http://colaboratory.googleapis.com/mcp")

        with self.assertRaises(ValueError):
            colab_mcp_client.ColabMCPConfig(mode="http", http_url="https://evil.example.com/mcp")

        stdout = (
            "Booting runtime...\n"
            f"{colab_mcp_client.JSON_START_MARKER}\n"
            '{"schema_version": "2.0", "ok": true}\n'
            f"{colab_mcp_client.JSON_END_MARKER}\n"
            "Done."
        )
        parsed = colab_mcp_client.extract_marked_json(stdout)
        self.assertEqual(parsed, {"schema_version": "2.0", "ok": True})

        with self.assertRaises(ValueError):
            colab_mcp_client.extract_marked_json("No markers present")

    def test_probe_transport_redacts_bearer_token(self):
        cfg = colab_mcp_client.ColabMCPConfig.from_env(
            {"COLAB_MCP_MODE": "http", "COLAB_MCP_BEARER_TOKEN": "super-secret-oauth-token-123"}
        )
        info = colab_mcp_client.probe_transport(cfg)
        self.assertTrue(info["http_token_configured"])
        self.assertNotIn("super-secret-oauth-token-123", json.dumps(info))

    def test_execute_python_script_hosted_and_stdio_tool_shapes(self):
        # 1. Hosted runtime/session/execute_code tool shape
        hosted_tools = [
            _FakeTool("create_runtime", {"accelerator": {"type": "string"}}),
            _FakeTool("create_session", {"runtime_id": {"type": "string"}}),
            _FakeTool("execute_code", {"runtime_id": {"type": "string"}, "session_id": {"type": "string"}, "code": {"type": "string"}}),
        ]
        captured_hosted = []

        def _hosted_handler(name, args):
            if name == "create_runtime":
                return _FakeResult(json.dumps({"runtime_id": "rt-99"}))
            if name == "create_session":
                return _FakeResult(json.dumps({"session_id": "sess-77"}))
            captured_hosted.append(args)
            return _FakeResult(f"{colab_mcp_client.JSON_START_MARKER}{json.dumps(_sample_vol_surface())}{colab_mcp_client.JSON_END_MARKER}")

        sess1 = _FakeMCPSession(hosted_tools, _hosted_handler)

        @asynccontextmanager
        async def _factory1(_cfg):
            yield sess1

        client1 = colab_mcp_client.ColabMCPClient(
            config=colab_mcp_client.ColabMCPConfig(mode="http", http_bearer_token="tok"),
            session_factory=_factory1,
        )
        out1 = client1.execute_python_script_sync("print('ok')", {"symbols": ["NVDA"]}, accelerator="CPU")
        self.assertEqual(out1["schema_version"], "2.0")
        self.assertEqual(sess1.calls[0][1]["accelerator"], "CPU")
        self.assertEqual(captured_hosted[0]["runtime_id"], "rt-99")
        self.assertEqual(captured_hosted[0]["session_id"], "sess-77")
        self.assertIn("GEM_JOB_INPUTS", captured_hosted[0]["code"])

        # 2. Local colab-mcp direct `run_code` tool shape
        stdio_tools = [_FakeTool("run_code", {"source": {"type": "string"}})]
        sess2 = _FakeMCPSession(
            stdio_tools,
            lambda _n, _a: _FakeResult(
                f"{colab_mcp_client.JSON_START_MARKER}{json.dumps(_sample_quant_scout())}{colab_mcp_client.JSON_END_MARKER}"
            ),
        )

        @asynccontextmanager
        async def _factory2(_cfg):
            yield sess2

        client2 = colab_mcp_client.ColabMCPClient(session_factory=_factory2)
        out2 = client2.execute_python_script_sync("print('ok')", {"symbols": ["PLTR"]})
        self.assertEqual(out2["universe_size"], 520)
        self.assertEqual(sess2.calls[0][0], "run_code")
        self.assertIn("source", sess2.calls[0][1])

    def test_execute_python_script_tool_error_and_timeout(self):
        err_sess = _FakeMCPSession(
            [_FakeTool("run_code", {"code": {"type": "string"}})],
            lambda _n, _a: _FakeResult("Runtime crashed", is_error=True),
        )

        @asynccontextmanager
        async def _err_factory(_cfg):
            yield err_sess

        client = colab_mcp_client.ColabMCPClient(session_factory=_err_factory)
        with self.assertRaises(RuntimeError):
            client.execute_python_script_sync("1/0", {"symbols": ["NVDA"]})

        async def _timeout_wait_for(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError()

        with patch("colab_mcp_client.asyncio.wait_for", side_effect=_timeout_wait_for):
            with self.assertRaises(asyncio.TimeoutError):
                client.execute_python_script_sync("import time; time.sleep(99)", {"symbols": ["NVDA"]})


class TestColabJobManagerAndAPI(unittest.TestCase):
    def test_job_manager_lifecycle_single_flight_and_cache_preservation(self):
        with tempfile.TemporaryDirectory() as td:
            gate = threading.Event()
            mock_svc = MagicMock()
            mock_svc.get_portfolio_basket.return_value = {"portfolio": [{"ticker": "NVDA", "shares": 5, "wac": 100.0}], "unallocated_cash_eur": 7777.0}
            mock_svc.get_watchlist.return_value = ["PLTR"]
            mock_svc.get_decision_log.return_value = []

            class _MockClient:
                def __init__(self, payload, fail=False):
                    self._payload = payload
                    self._fail = fail

                def execute_python_script_sync(self, script_source, job_inputs, accelerator="CPU"):
                    self._last_inputs = job_inputs
                    gate.wait(timeout=2.0)
                    if self._fail:
                        raise RuntimeError("Simulated Colab disconnect")
                    return self._payload

            client_ref = [_MockClient(_sample_vol_surface())]
            mgr = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="stdio"),
                client_factory=lambda _cfg: client_ref[0],
                worker_loader=lambda _jt: "print('worker')",
                service_provider=lambda: mock_svc,
            )

            job = mgr.submit_job("vol_surface")
            self.assertEqual(job["status"], "queued")

            # Single-flight conflict while job is blocked on gate
            with self.assertRaises(colab_jobs.ColabJobConflictError):
                mgr.submit_job("vol_surface")

            gate.set()
            for _ in range(50):
                cur = mgr.get_job(job["job_id"])
                if cur["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)

            done = mgr.get_job(job["job_id"])
            self.assertEqual(done["status"], "succeeded")
            self.assertEqual(done["artifact_summary"]["artifact_type"], "vol_surface")
            self.assertNotIn("7777.0", json.dumps(client_ref[0]._last_inputs))
            self.assertIsNotNone(colab_calibration.load_colab_artifact("vol_surface", cache_dir=td))

            # Second failing run must preserve prior valid cache
            client_ref[0] = _MockClient({}, fail=True)
            job2 = mgr.submit_job("vol_surface")
            for _ in range(50):
                cur2 = mgr.get_job(job2["job_id"])
                if cur2["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)
            self.assertEqual(cur2["status"], "failed")
            self.assertIsNotNone(colab_calibration.load_colab_artifact("vol_surface", cache_dir=td))

            # Offline mode blocks live job submission
            with self.assertRaises(ValueError):
                mgr.submit_job("quant_scout", mode="offline")

    def test_fastapi_colab_endpoints(self):
        client = TestClient(web_server.app)
        with tempfile.TemporaryDirectory() as td:
            mock_svc = MagicMock()
            mock_svc.get_portfolio_basket.return_value = {"portfolio": [], "unallocated_cash_eur": 0.0}
            mock_svc.get_watchlist.return_value = ["NVDA"]
            mock_svc.get_decision_log.return_value = []

            gate = threading.Event()

            class _SlowClient:
                def execute_python_script_sync(self, script_source, job_inputs, accelerator="CPU"):
                    gate.wait(timeout=2.0)
                    return _sample_quant_scout()

            mgr = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="stdio"),
                client_factory=lambda _cfg: _SlowClient(),
                worker_loader=lambda _jt: "print('scout')",
                service_provider=lambda: mock_svc,
            )

            orig_import = colab_calibration.import_colab_artifact
            orig_load = colab_calibration.load_colab_artifact
            orig_status = colab_calibration.get_artifact_status

            with patch.object(web_server, "get_colab_job_manager", return_value=mgr), \
                 patch.object(web_server, "import_colab_artifact", side_effect=lambda at, p: orig_import(at, p, cache_dir=td)), \
                 patch.object(web_server, "load_colab_artifact", side_effect=lambda at: orig_load(at, cache_dir=td)), \
                 patch.object(web_server, "get_artifact_status", side_effect=lambda at: orig_status(at, cache_dir=td)):

                st = client.get("/api/colab/status")
                self.assertEqual(st.status_code, 200)
                self.assertIn("transport", st.json())
                self.assertIn("artifacts", st.json())

                bad_type = client.post("/api/colab/jobs/nonexistent_job")
                self.assertEqual(bad_type.status_code, 400)

                off_resp = client.post("/api/colab/jobs/quant_scout", json={"mode": "offline"})
                self.assertEqual(off_resp.status_code, 422)

                launch = client.post("/api/colab/jobs/quant_scout", json={"options": {"top_n": 15}})
                self.assertEqual(launch.status_code, 202)
                job_id = launch.json()["job_id"]

                dup = client.post("/api/colab/jobs/quant_scout")
                self.assertEqual(dup.status_code, 409)

                gate.set()
                for _ in range(50):
                    poll = client.get(f"/api/colab/jobs/{job_id}")
                    self.assertEqual(poll.status_code, 200)
                    if poll.json()["status"] in {"succeeded", "failed"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(poll.json()["status"], "succeeded")

                self.assertEqual(client.get("/api/colab/jobs/colab-missing").status_code, 404)

                # Offline artifact import & read-only GET
                bad_imp = client.post("/api/colab/artifacts/vol_surface/import", json={"schema_version": "1.0"})
                self.assertEqual(bad_imp.status_code, 422)
                self.assertEqual(client.post("/api/colab/artifacts/unknown/import", json={}).status_code, 404)

                ok_imp = client.post("/api/colab/artifacts/vol_surface/import", json=_sample_vol_surface())
                self.assertEqual(ok_imp.status_code, 200)
                self.assertEqual(ok_imp.json()["artifact_type"], "vol_surface")

                get_art = client.get("/api/colab/artifacts/vol_surface")
                self.assertEqual(get_art.status_code, 200)
                self.assertTrue(get_art.json()["loaded"])
                self.assertEqual(get_art.json()["data"]["tickers"]["NVDA"]["dealer_flow_regime"], "VANNA_TAILWIND")
                self.assertEqual(client.get("/api/colab/artifacts/unknown").status_code, 404)

    def test_run_pipeline_returns_setup_required_when_transport_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mgr = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="stdio"),
                transport_prober=lambda _cfg: {"mode": "stdio", "ready": False, "setup_hint": "Install uv first"},
            )
            pipe = mgr.run_pipeline(only_stale=True, trigger="startup")
            self.assertEqual(pipe["state"], "setup_required")
            self.assertEqual(pipe["percent"], 0)
            self.assertEqual(pipe["step_label"], "Install uv first")
            self.assertEqual(pipe["jobs"]["vol_surface"]["status"], "setup_required")

    def test_run_pipeline_skips_fresh_and_runs_only_stale_sequentially(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            # Pre-seed 2 fresh artifacts (vol_surface, quant_scout); leave sec_nlp & walk_forward missing
            colab_calibration.import_colab_artifact("vol_surface", _sample_vol_surface(), cache_dir=td)
            colab_calibration.import_colab_artifact("quant_scout", _sample_quant_scout(), cache_dir=td)

            mock_svc = MagicMock()
            mock_svc.get_portfolio_basket.return_value = {"portfolio": [], "unallocated_cash_eur": 0.0}
            mock_svc.get_watchlist.return_value = ["NVDA"]
            mock_svc.get_decision_log.return_value = []

            executed_types = []

            class _SeqClient:
                def execute_python_script_sync(self, script_source, job_inputs, accelerator="CPU"):
                    jt = job_inputs["job_type"]
                    executed_types.append(jt)
                    if jt == "sec_nlp":
                        return _sample_sec_nlp()
                    if jt == "walk_forward":
                        return _sample_backtest_evidence()
                    return _sample_vol_surface()

            mgr = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="stdio"),
                client_factory=lambda _cfg: _SeqClient(),
                worker_loader=lambda jt: f"print({jt!r})",
                service_provider=lambda: mock_svc,
            )
            pipe = mgr.run_pipeline(only_stale=True, trigger="startup")
            self.assertIn(pipe["state"], {"running", "succeeded"})

            for _ in range(100):
                status = mgr.get_status()["pipeline"]
                if status["state"] in {"succeeded", "partial", "failed"}:
                    break
                time.sleep(0.02)

            final_pipe = mgr.get_status()["pipeline"]
            self.assertEqual(final_pipe["state"], "succeeded")
            self.assertEqual(final_pipe["percent"], 100)
            self.assertEqual(final_pipe["skipped_fresh_count"], 2)
            self.assertEqual(final_pipe["jobs"]["vol_surface"]["status"], "skipped_fresh")
            self.assertEqual(final_pipe["jobs"]["quant_scout"]["status"], "skipped_fresh")
            self.assertEqual(final_pipe["jobs"]["sec_nlp"]["status"], "succeeded")
            self.assertEqual(final_pipe["jobs"]["walk_forward"]["status"], "succeeded")
            self.assertEqual(executed_types, ["sec_nlp", "walk_forward"])

    def test_maybe_start_startup_pipeline_respects_disable_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mgr = colab_jobs.ColabJobManager(cache_dir=td)
            with patch.dict(os.environ, {"COLAB_AUTO_RUN_ON_STARTUP": "0"}):
                res = mgr.maybe_start_startup_pipeline()
            self.assertEqual(res["state"], "disabled")

    def test_maybe_start_startup_pipeline_is_opt_in_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mgr = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="stdio"),
                transport_prober=lambda _cfg: {"mode": "stdio", "ready": False, "setup_hint": "Install uv"},
            )
            env = {k: v for k, v in os.environ.items() if k != "COLAB_AUTO_RUN_ON_STARTUP"}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(mgr.maybe_start_startup_pipeline()["state"], "disabled")
            with patch.dict(os.environ, {"COLAB_AUTO_RUN_ON_STARTUP": "1"}):
                self.assertEqual(mgr.maybe_start_startup_pipeline()["state"], "setup_required")

    def test_persistent_session_reuses_one_open_and_restarts_after_close(self) -> None:
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        opens = []

        class _Sess:
            def __init__(self, idx):
                self.idx = idx

            async def initialize(self):
                pass

        @asynccontextmanager
        async def _opener():
            sess = _Sess(len(opens) + 1)
            opens.append(sess)
            yield sess

        ps = colab_mcp_client.PersistentColabSession(opener=_opener)
        try:
            async def _body(session):
                await asyncio.sleep(0)
                return session.idx

            linked_client = SimpleNamespace(_browser_linked=True)
            self.assertEqual(ps.run(_body, timeout=5, client=linked_client), 1)
            self.assertEqual(ps.run(_body, timeout=5), 1)
            self.assertEqual(len(opens), 1)
            st = ps.status()
            self.assertTrue(st["alive"])
            self.assertTrue(st["browser_linked"])

            async def _hang(_session):
                await asyncio.sleep(30)

            with self.assertRaises(asyncio.TimeoutError):
                ps.run(_hang, timeout=0.3)
            self.assertFalse(ps.status()["alive"])

            self.assertEqual(ps.run(_body, timeout=5), 2)
            self.assertEqual(len(opens), 2)
            self.assertFalse(ps.status()["browser_linked"])
        finally:
            ps.close()
        self.assertFalse(ps.is_alive())

    def test_persistent_session_start_failure_surfaces_root_cause(self) -> None:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _bad_opener():
            raise RuntimeError("uvx exploded")
            yield  # pragma: no cover

        ps = colab_mcp_client.PersistentColabSession(opener=_bad_opener)

        async def _body(_s):
            return 1

        with self.assertRaises(RuntimeError) as ctx:
            ps.run(_body, timeout=5)
        self.assertIn("uvx exploded", str(ctx.exception))

    def test_persistent_session_toggle_and_routing(self) -> None:
        self.assertTrue(colab_mcp_client.is_persistent_session_enabled({}))
        self.assertFalse(colab_mcp_client.is_persistent_session_enabled({"COLAB_MCP_PERSISTENT_SESSION": "0"}))
        stdio_client = colab_mcp_client.ColabMCPClient(config=colab_mcp_client.ColabMCPConfig(mode="stdio"))
        with patch.dict(os.environ, {"COLAB_MCP_PERSISTENT_SESSION": "1"}):
            self.assertTrue(stdio_client._use_persistent_session())
        with patch.dict(os.environ, {"COLAB_MCP_PERSISTENT_SESSION": "0"}):
            self.assertFalse(stdio_client._use_persistent_session())
        injected = colab_mcp_client.ColabMCPClient(
            config=colab_mcp_client.ColabMCPConfig(mode="stdio"),
            session_factory=lambda _c: None,
        )
        self.assertFalse(injected._use_persistent_session())
        http_client = colab_mcp_client.ColabMCPClient(
            config=colab_mcp_client.ColabMCPConfig(mode="http", http_bearer_token="t"),
        )
        self.assertFalse(http_client._use_persistent_session())

    def test_run_all_endpoint_accepts_request_and_rejects_unknown_keys(self) -> None:
        from fastapi.testclient import TestClient
        import web_server

        with tempfile.TemporaryDirectory() as td:
            mgr = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="stdio"),
                transport_prober=lambda _cfg: {"mode": "stdio", "ready": False, "setup_hint": "Install uv"},
            )
            with patch.object(web_server, "get_colab_job_manager", return_value=mgr):
                client = TestClient(web_server.app)
                bad = client.post("/api/colab/jobs/run_all", json={"unexpected": True})
                self.assertEqual(bad.status_code, 422)

                ok = client.post("/api/colab/jobs/run_all", json={"only_stale": False})
                self.assertEqual(ok.status_code, 202)
                self.assertEqual(ok.json()["state"], "setup_required")
                st = client.get("/api/colab/status")
                self.assertEqual(st.status_code, 200)
                self.assertIn("pipeline", st.json())

    def test_unwrap_exception_message_unpacks_nested_exception_groups(self) -> None:
        nested = ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [ExceptionGroup("inner", [RuntimeError("Google Colab browser session did not connect")])],
        )
        msg = colab_mcp_client.unwrap_exception_message(nested)
        self.assertEqual(msg, "Google Colab browser session did not connect")

    def test_browser_handshake_unlocks_tools_and_supports_snake_case_input_schema(self) -> None:
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        calls = []

        class _SnakeTool:
            def __init__(self, name, input_schema=None):
                self.name = name
                self.input_schema = input_schema or {}

        class _HandshakeSession:
            def __init__(self):
                self.unlocked = False
                self.init_count = 0

            async def initialize(self):
                self.init_count += 1

            async def list_tools(self):
                if not self.unlocked:
                    return SimpleNamespace(tools=[_SnakeTool("open_colab_browser_connection")])
                return SimpleNamespace(tools=[
                    _SnakeTool("open_colab_browser_connection"),
                    _SnakeTool("execute_code", {"properties": {"python_code": {"type": "string"}}}),
                ])

            async def call_tool(self, name, args):
                calls.append((name, args))
                if name == "open_colab_browser_connection":
                    self.unlocked = True
                    return SimpleNamespace(
                        isError=False,
                        content=[SimpleNamespace(text="true")],
                        structured_content={"result": True},
                    )
                if name == "execute_code":
                    return SimpleNamespace(
                        isError=False,
                        content=[SimpleNamespace(text=(
                            f"{colab_mcp_client.JSON_START_MARKER}"
                            '{"ok": true, "mode": "handshake"}'
                            f"{colab_mcp_client.JSON_END_MARKER}"
                        ))],
                    )
                raise AssertionError(f"Unexpected tool call: {name}")

        sess = _HandshakeSession()

        @asynccontextmanager
        async def _factory(_cfg):
            yield sess

        client = colab_mcp_client.ColabMCPClient(
            config=colab_mcp_client.ColabMCPConfig(mode="stdio"),
            session_factory=_factory,
        )
        out = client.execute_python_script_sync("print('hi')", {"job_type": "vol_surface", "symbols": ["NVDA"]})
        self.assertEqual(out, {"ok": True, "mode": "handshake"})
        self.assertEqual(calls[0][0], "open_colab_browser_connection")
        self.assertEqual(calls[1][0], "execute_code")
        self.assertIn("python_code", calls[1][1])

    def test_browser_handshake_false_raises_plain_english_guidance(self) -> None:
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        class _TimeoutSession:
            async def initialize(self):
                pass

            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(name="open_colab_browser_connection", input_schema={})])

            async def call_tool(self, name, args):
                return SimpleNamespace(
                    isError=False,
                    content=[SimpleNamespace(text="false")],
                    structured_content={"result": False},
                )

        @asynccontextmanager
        async def _factory(_cfg):
            yield _TimeoutSession()

        client = colab_mcp_client.ColabMCPClient(
            config=colab_mcp_client.ColabMCPConfig(mode="stdio"),
            session_factory=_factory,
        )
        with self.assertRaises(RuntimeError) as ctx:
            client.execute_python_script_sync("print('hi')", {"job_type": "vol_surface", "symbols": ["NVDA"]})
        self.assertIn("Google Colab browser session did not connect", str(ctx.exception))

    def test_execute_scripts_batch_sync_reuses_single_session(self) -> None:
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        session_opens = 0
        executed = []

        class _BatchSession:
            async def initialize(self):
                pass

            async def list_tools(self):
                return SimpleNamespace(tools=[
                    SimpleNamespace(name="execute_code", input_schema={"properties": {"code": {"type": "string"}}}),
                ])

            async def call_tool(self, name, args):
                executed.append(args["code"])
                idx = len(executed)
                return SimpleNamespace(
                    isError=False,
                    content=[SimpleNamespace(text=(
                        f"{colab_mcp_client.JSON_START_MARKER}"
                        f'{{"step": {idx}}}'
                        f"{colab_mcp_client.JSON_END_MARKER}"
                    ))],
                )

        @asynccontextmanager
        async def _factory(_cfg):
            nonlocal session_opens
            session_opens += 1
            yield _BatchSession()

        client = colab_mcp_client.ColabMCPClient(
            config=colab_mcp_client.ColabMCPConfig(mode="stdio"),
            session_factory=_factory,
        )
        res = client.execute_scripts_batch_sync([
            ("vol_surface", "print(1)", {"job_type": "vol_surface", "symbols": ["NVDA"]}, "CPU"),
            ("sec_nlp", "print(2)", {"job_type": "sec_nlp", "symbols": ["NVDA"]}, "T4"),
        ])
        self.assertEqual(session_opens, 1)
        self.assertTrue(res["vol_surface"]["ok"])
        self.assertTrue(res["sec_nlp"]["ok"])
        self.assertEqual(res["vol_surface"]["payload"], {"step": 1})
        self.assertEqual(res["sec_nlp"]["payload"], {"step": 2})

    def test_sitecustomize_hook_captures_allowed_colab_url_and_rejects_untrusted_hosts(self) -> None:
        import subprocess

        hook_dir = Path(__file__).resolve().parent.parent / "python" / "colab_hook"
        self.assertEqual(sorted(p.name for p in hook_dir.iterdir() if p.name != "__pycache__"), ["sitecustomize.py"])
        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "url.txt"
            env = dict(os.environ)
            env["GEM_COLAB_CONNECT_URL_FILE"] = str(out_file)
            env["GEM_COLAB_SKIP_OS_OPEN"] = "1"
            env["PYTHONPATH"] = str(hook_dir)

            good_url = "https://colab.research.google.com/notebooks/empty.ipynb#mcpProxyToken=tok&mcpProxyPort=5555"
            code = (
                "import webbrowser, sys\n"
                "ok = webbrowser.open_new(sys.argv[1])\n"
                "webbrowser.open_new('https://evil.example.com/phish')\n"
                "webbrowser.open_new_tab('http://colab.research.google.com/insecure')\n"
                "sys.exit(0 if ok else 3)\n"
            )
            proc = subprocess.run(
                [sys.executable, "-c", code, good_url],
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # Untrusted / non-https URLs must not overwrite the valid captured URL
            self.assertEqual(out_file.read_text(encoding="utf-8").strip(), good_url)

    def test_browser_handshake_invokes_connect_url_callback(self) -> None:
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        seen_urls = []
        target_url = "https://colab.research.google.com/notebooks/empty.ipynb#mcpProxyToken=abc&mcpProxyPort=51277"

        with tempfile.NamedTemporaryFile(prefix="gem-test-url-", suffix=".txt", delete=False) as tf:
            tmp_path = tf.name
        try:
            class _BridgeSession:
                def __init__(self):
                    self.unlocked = False

                async def initialize(self):
                    pass

                async def list_tools(self):
                    if not self.unlocked:
                        return SimpleNamespace(tools=[SimpleNamespace(name="open_colab_browser_connection", input_schema={})])
                    return SimpleNamespace(tools=[
                        SimpleNamespace(name="execute_code", input_schema={"properties": {"code": {"type": "string"}}}),
                    ])

                async def call_tool(self, name, args):
                    if name == "open_colab_browser_connection":
                        Path(tmp_path).write_text(target_url, encoding="utf-8")
                        await asyncio.sleep(0.25)
                        self.unlocked = True
                        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="true")], structured_content={"result": True})
                    return SimpleNamespace(
                        isError=False,
                        content=[SimpleNamespace(text=f"{colab_mcp_client.JSON_START_MARKER}{{\"ok\":true}}{colab_mcp_client.JSON_END_MARKER}")],
                    )

            @asynccontextmanager
            async def _factory(_cfg):
                yield _BridgeSession()

            client = colab_mcp_client.ColabMCPClient(
                config=colab_mcp_client.ColabMCPConfig(mode="stdio"),
                session_factory=_factory,
            )
            client._connect_url_file = tmp_path
            out = client.execute_python_script_sync(
                "print(1)",
                {"job_type": "vol_surface", "symbols": ["NVDA"]},
                connect_url_callback=lambda u: seen_urls.append(u),
            )
            self.assertEqual(out, {"ok": True})
            self.assertIn(target_url, seen_urls)
            self.assertIsNone(seen_urls[-1])
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_wrap_script_preserves_future_imports_across_all_workers(self) -> None:
        workers_dir = Path(__file__).resolve().parent.parent / "python" / "colab_workers"
        worker_files = (
            "vol_surface_worker.py",
            "quant_scout_worker.py",
            "sec_nlp_worker.py",
            "walk_forward_worker.py",
        )
        for name in worker_files:
            src = (workers_dir / name).read_text(encoding="utf-8")
            wrapped = colab_mcp_client.ColabMCPClient._wrap_script(src, {"symbols": ["NVDA"], "job_type": name})
            # Must compile without SyntaxError (`from __future__` must stay above imports/assignments)
            compile(wrapped, name, "exec")
            future_idx = wrapped.index("from __future__ import annotations")
            inputs_idx = wrapped.index("GEM_JOB_INPUTS = ")
            self.assertLess(future_idx, inputs_idx, msg=f"{name} placed GEM_JOB_INPUTS before __future__")

    def test_from_env_autodetects_cli_when_colab_installed_else_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake_exe = Path(td) / ("colab.exe" if os.name == "nt" else "colab")
            fake_exe.write_text("", encoding="utf-8")
            cfg_cli = colab_mcp_client.ColabMCPConfig.from_env({"COLAB_CLI_PATH": str(fake_exe)})
            self.assertEqual(cfg_cli.mode, "cli")

        cfg_no_cli = colab_mcp_client.ColabMCPConfig.from_env({"PATH": ""})
        self.assertEqual(cfg_no_cli.mode, "stdio")

        with self.assertRaises(ValueError):
            colab_mcp_client.ColabMCPConfig(mode="cli", cli_auth="invalid-auth")

    def test_cli_mode_executes_ephemeral_run_allowlists_gpus_and_aborts_on_auth_error(self) -> None:
        from types import SimpleNamespace

        recorded_cmds = []

        def _fake_run(cmd, **_kwargs):
            recorded_cmds.append(list(cmd))
            if "T4" in cmd:
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"{colab_mcp_client.JSON_START_MARKER}{{\"step\":\"t4\"}}{colab_mcp_client.JSON_END_MARKER}",
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=f"{colab_mcp_client.JSON_START_MARKER}{{\"step\":\"cpu\"}}{colab_mcp_client.JSON_END_MARKER}",
                stderr="",
            )

        cfg = colab_mcp_client.ColabMCPConfig(mode="cli", cli_path="/mock/colab", cli_auth="oauth2", timeout_sec=300)
        client = colab_mcp_client.ColabMCPClient(config=cfg)
        with patch("colab_mcp_client.find_colab_cli", return_value="/mock/colab"), \
             patch("colab_mcp_client.subprocess.run", side_effect=_fake_run):
            out = client.execute_python_script_sync("from __future__ import annotations\nprint(1)", {"symbols": ["NVDA"]}, accelerator="CPU")
            self.assertEqual(out, {"step": "cpu"})
            self.assertIn("--config", recorded_cmds[0])
            self.assertIn("--auth", recorded_cmds[0])
            self.assertIn("oauth2", recorded_cmds[0])
            self.assertIn("run", recorded_cmds[0])
            self.assertIn("--timeout", recorded_cmds[0])
            self.assertIn("300", recorded_cmds[0])
            self.assertNotIn("--gpu", recorded_cmds[0])

            # Reject unapproved accelerators so colab-cli cannot silently fall back to A100
            with self.assertRaises(ValueError):
                client.execute_python_script_sync("print(1)", {"symbols": ["NVDA"]}, accelerator="A100")

            # Batch mode runs sequentially via CLI and honors T4
            events = []
            batch = client.execute_scripts_batch_sync(
                [
                    ("vol_surface", "print(1)", {"symbols": ["NVDA"]}, "CPU"),
                    ("sec_nlp", "print(2)", {"symbols": ["NVDA"]}, "T4"),
                ],
                progress_callback=lambda jt, ph, msg: events.append((jt, ph)),
            )
            self.assertTrue(batch["vol_surface"]["ok"])
            self.assertTrue(batch["sec_nlp"]["ok"])
            self.assertEqual(batch["sec_nlp"]["payload"], {"step": "t4"})
            self.assertIn("--gpu", recorded_cmds[-1])
            self.assertIn("T4", recorded_cmds[-1])
            self.assertIn(("sec_nlp", "succeeded"), events)

        # Auth error surfaces `colab sessions` hint and aborts remaining batch items
        def _fake_auth_fail(_cmd, **_kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="401 Unauthorized: invalid credentials")

        with patch("colab_mcp_client.find_colab_cli", return_value="/mock/colab"), \
             patch("colab_mcp_client.subprocess.run", side_effect=_fake_auth_fail):
            bad_batch = client.execute_scripts_batch_sync([
                ("vol_surface", "print(1)", {"symbols": ["NVDA"]}, "CPU"),
                ("quant_scout", "print(2)", {"symbols": ["NVDA"]}, "CPU"),
            ])
            self.assertFalse(bad_batch["vol_surface"]["ok"])
            self.assertIn("colab sessions", bad_batch["vol_surface"]["error"])
            self.assertEqual(bad_batch["quant_scout"]["error"], bad_batch["vol_surface"]["error"])

    def test_persistent_session_resets_when_handshake_times_out(self) -> None:
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        opens = []

        class _Sess:
            async def initialize(self):
                pass

        @asynccontextmanager
        async def _opener():
            s = SimpleNamespace(idx=len(opens) + 1)
            opens.append(s)
            yield _Sess()

        ps = colab_mcp_client.PersistentColabSession(opener=_opener)
        try:
            fake_client = SimpleNamespace(_browser_linked=False, _session_broken=True)

            async def _fail_handshake(_session):
                raise colab_mcp_client.ColabHandshakeTimeoutError("timed out")

            with self.assertRaises(colab_mcp_client.ColabHandshakeTimeoutError):
                ps.run(_fail_handshake, timeout=5, client=fake_client)
            self.assertFalse(ps.is_alive())
            self.assertFalse(ps.status()["alive"])
        finally:
            ps.close()

    def test_ai_scout_sectors_routes_via_colab_when_ready_and_falls_back_when_offline(self) -> None:
        from fastapi.testclient import TestClient
        import web_server

        with tempfile.TemporaryDirectory() as td:
            mock_svc = MagicMock()
            mock_svc.get_scout_categories.return_value = ["Biotech", "Semiconductors"]
            mock_svc.get_portfolio_basket.return_value = {"portfolio": [], "unallocated_cash_eur": 0.0}
            mock_svc.get_watchlist.return_value = []
            mock_svc.get_decision_log.return_value = []

            captured = []

            class _CliFake:
                def execute_python_script_sync(self, _src, job_inputs, accelerator="CPU"):
                    captured.append(job_inputs)
                    return _sample_quant_scout()

            mgr_ready = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="cli"),
                client_factory=lambda _cfg: _CliFake(),
                worker_loader=lambda _jt: "print('sector')",
                service_provider=lambda: mock_svc,
                transport_prober=lambda _cfg: {"mode": "cli", "ready": True},
            )
            client = TestClient(web_server.app)
            with patch.object(web_server, "get_persistence_service", return_value=mock_svc), \
                 patch.object(web_server, "get_colab_job_manager", return_value=mgr_ready):
                resp = client.post("/api/ai_scout", json={"mode": "sectors", "limit": 3, "max_rsi": 70})
                self.assertEqual(resp.status_code, 202)
                body = resp.json()
                self.assertEqual(body["engine"], "colab")
                self.assertEqual(body["sectors"], ["Biotech", "Semiconductors"])
                job_id = body["colab_job_id"]
                for _ in range(50):
                    j = mgr_ready.get_job(job_id)
                    if j and j["status"] in {"succeeded", "failed"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(mgr_ready.get_job(job_id)["status"], "succeeded")
                self.assertEqual(captured[0]["options"]["sectors"], ["Biotech", "Semiconductors"])
                self.assertEqual(captured[0]["options"]["max_rsi"], 70)

            mgr_offline = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="offline"),
                transport_prober=lambda _cfg: {"mode": "offline", "ready": True},
            )
            with patch.object(web_server, "get_persistence_service", return_value=mock_svc), \
                 patch.object(web_server, "get_colab_job_manager", return_value=mgr_offline), \
                 patch.object(web_server, "_trigger_force_refresh"), \
                 patch.object(web_server, "reload_scout_tickers_task"):
                resp_off = client.post("/api/ai_scout", json={"mode": "sectors", "limit": 2, "max_rsi": 75})
                self.assertEqual(resp_off.status_code, 200)
                self.assertEqual(resp_off.json()["engine"], "local")

    def test_retry_failed_only_reruns_only_failed_jobs_and_preserves_succeeded(self) -> None:
        from fastapi.testclient import TestClient
        import colab_calibration
        import web_server

        with tempfile.TemporaryDirectory() as td:
            mock_svc = MagicMock()
            mock_svc.get_portfolio_basket.return_value = {"portfolio": [], "unallocated_cash_eur": 0.0}
            mock_svc.get_watchlist.return_value = ["NVDA"]
            mock_svc.get_decision_log.return_value = []

            run_log = []
            should_fail_scout = [True]

            class _PartialClient:
                def execute_scripts_batch_sync(self, items, progress_callback=None, connect_url_callback=None):
                    out = {}
                    for jt, _src, _inp, _acc in items:
                        run_log.append(jt)
                        if jt == "quant_scout" and should_fail_scout[0]:
                            out[jt] = {"ok": False, "error": "Transient Yahoo timeout"}
                        elif jt == "vol_surface":
                            out[jt] = {"ok": True, "payload": _sample_vol_surface()}
                        elif jt == "sec_nlp":
                            out[jt] = {"ok": True, "payload": _sample_sec_nlp()}
                        elif jt == "quant_scout":
                            out[jt] = {"ok": True, "payload": _sample_quant_scout()}
                        else:
                            out[jt] = {"ok": True, "payload": _sample_backtest_evidence()}
                    return out

            mgr = colab_jobs.ColabJobManager(
                cache_dir=td,
                config_loader=lambda: colab_mcp_client.ColabMCPConfig(mode="cli"),
                client_factory=lambda _cfg: _PartialClient(),
                worker_loader=lambda jt: f"print({jt!r})",
                service_provider=lambda: mock_svc,
                transport_prober=lambda _cfg: {"mode": "cli", "ready": True},
            )
            client = TestClient(web_server.app)
            with patch.object(web_server, "get_colab_job_manager", return_value=mgr):
                first = client.post("/api/colab/jobs/run_all", json={"only_stale": False})
                self.assertEqual(first.status_code, 202)
                for _ in range(50):
                    if mgr.get_status()["pipeline"]["state"] in {"partial", "succeeded", "failed"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(mgr.get_status()["pipeline"]["state"], "partial")
                self.assertEqual(set(run_log), {"vol_surface", "sec_nlp", "quant_scout", "walk_forward"})

                run_log.clear()
                should_fail_scout[0] = False
                retry = client.post("/api/colab/jobs/run_all", json={"only_stale": False, "retry_failed_only": True})
                self.assertEqual(retry.status_code, 202)
                for _ in range(50):
                    if mgr.get_status()["pipeline"]["state"] in {"partial", "succeeded", "failed"}:
                        break
                    time.sleep(0.02)

                final_pipe = mgr.get_status()["pipeline"]
                self.assertEqual(final_pipe["state"], "succeeded")
                # Only the failed job (`quant_scout`) re-ran; all 3 succeeded jobs were preserved
                self.assertEqual(run_log, ["quant_scout"])
                self.assertEqual(final_pipe["jobs"]["vol_surface"]["status"], "succeeded")
                self.assertEqual(final_pipe["jobs"]["sec_nlp"]["status"], "succeeded")
                self.assertEqual(final_pipe["jobs"]["walk_forward"]["status"], "succeeded")
                self.assertEqual(final_pipe["jobs"]["quant_scout"]["status"], "succeeded")
                self.assertIsNotNone(colab_calibration.load_colab_artifact("vol_surface", cache_dir=td))

    def test_premarket_intraday_vwap_and_gex_fallbacks(self) -> None:
        from types import SimpleNamespace
        from fastapi.testclient import TestClient
        import pandas as pd
        import fetch_stocks as fs
        import web_server

        # 1. `/api/intraday/{symbol}` works cleanly (`asyncio` imported) and returns sorted deduplicated bars
        mock_payload = {"bars": [{"time": 1700000000, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 0}], "warmup_closes": [99.0], "symbol": "NVDA"}
        client = TestClient(web_server.app)
        with patch.object(fs, "_fetch_intraday_payload", return_value=mock_payload):
            res = client.get("/api/intraday/NVDA")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(len(res.json()["bars"]), 1)
            self.assertNotIn("error", res.json())

        # 2. Pre-market VWAP preserves valid OHLC price bars when Yahoo emits volume=0
        ny_now = fs.datetime.now(fs._NY_TZ).replace(hour=6, minute=15, second=0, microsecond=0)
        ts1 = int((ny_now.replace(hour=5, minute=30)).timestamp())
        ts2 = int((ny_now.replace(hour=5, minute=31)).timestamp())
        fake_chart = SimpleNamespace(json=lambda: {
            "chart": {"result": [{
                "timestamp": [ts1, ts2],
                "indicators": {"quote": [{"high": [250.0, 252.0], "low": [249.0, 251.0], "close": [249.5, 251.5], "volume": [0, 0]}]},
            }]}
        })

        class _FakeDt(fs.datetime):
            @classmethod
            def now(cls, tz=None):
                return ny_now

        with patch.object(fs, "datetime", _FakeDt), \
             patch.object(fs, "safe_yf_get", return_value=fake_chart):
            valid, obs_iso, before_anchor = fs._acquire_true_intraday_vwap_bars("AMZN", "PRE-MARKET")
            self.assertFalse(before_anchor)
            self.assertEqual(len(valid), 2)
            self.assertIsNotNone(obs_iso)

        # 3. Pre-market GEX substitutes valid median IV when Yahoo reports placeholder IV<=0.05
        calls_df = pd.DataFrame([
            {"strike": 250.0, "openInterest": 1000, "volume": 0, "impliedVolatility": 0.00001},
            {"strike": 255.0, "openInterest": 500, "volume": 0, "impliedVolatility": 0.36},
        ])
        puts_df = pd.DataFrame([
            {"strike": 245.0, "openInterest": 800, "volume": 0, "impliedVolatility": 0.00001},
        ])
        fake_ticker = SimpleNamespace(
            options=["2099-01-16"],
            option_chain=lambda _exp: SimpleNamespace(calls=calls_df, puts=puts_df),
            fast_info=SimpleNamespace(three_month_average_volume=50_000_000),
        )
        obs = fs._acquire_options_gex_raw("AMZN", ticker_obj=fake_ticker, spot_price=250.0)
        self.assertEqual(obs.data["contract_count"], 3)
        self.assertNotEqual(obs.data["gex_profile"]["net_gex"], 0.0)


if __name__ == "__main__":
    unittest.main()
