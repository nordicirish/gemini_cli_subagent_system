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


if __name__ == "__main__":
    unittest.main()
