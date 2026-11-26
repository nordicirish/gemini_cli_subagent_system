"""
Unit & Integration Tests for Stage B Optimizations:
- Unified agent_factory runtime parity & analytics context sharing
- Concurrent READ vs sequential WRITE ToolDispatcher execution + cancel_check forwarding
- Forensic search 120s TTL memoization & early cancel abort
- MarketDataCache.garbage_collect() complete per-ticker dict pruning
- Multi-tab SSE log fan-out & unsubscribe cleanup
- SqlitePersistenceAdapter mtime base-SSoT cache & single-connection get_daemon_cycle_snapshot()
- Intraday 30s TTL cache & build_compact_intraday_1m_summary()
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import agent_factory
from agent_framework import AgentFramework, AntigravityResponse
from audit_logger import AuditLogger
import fetch_stocks
from python.database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE
from python.persistence.sqlite_adapter import SqlitePersistenceAdapter
from tool_dispatcher import ToolDispatcher, get_active_tool_cancel_check
from tool_registry import ToolRegistry
import tools
import web_server


class _MockPartFactory:
    @staticmethod
    def from_function_response(name: str, response: dict):
        return SimpleNamespace(name=name, response=response, kind="function_response")


class TestStageBOptimizations(unittest.TestCase):
    def test_agent_factory_runtime_parity_and_single_context_load(self):
        framework = AgentFramework()
        loader_calls = 0

        def fake_loader():
            nonlocal loader_calls
            loader_calls += 1
            return ({"tickers": []}, {}, {"available": False}, None)

        bundle = agent_factory.build_agent_runtime(framework, analytics_context_loader=fake_loader)
        tool_names = {getattr(t, "__name__", "") for t in bundle.terminal_tools}

        self.assertIn("refresh_analytics", tool_names)
        self.assertIn("ask_state_validation_router", tool_names)
        self.assertIn("ask_context_engine", tool_names)
        self.assertIn("ask_post-trade_review_engine", tool_names)
        self.assertIn("ask_council", tool_names)
        for collapsed in (
            "ask_regime_engine",
            "ask_strategy_engine",
            "ask_gex_engine",
            "ask_execution_engine",
            "ask_rule_enforcer_engine",
            "ask_technical_validator",
        ):
            self.assertNotIn(collapsed, tool_names)
            self.assertIn(collapsed, bundle.council_members)

        with patch.object(agent_factory.ANALYTICS_ENGINE, "format_gex_engine_response", return_value="GEX OK") as mock_gex:
            out = bundle.gex_engine("status")
        self.assertEqual(out, "GEX OK")
        self.assertEqual(loader_calls, 1)
        mock_gex.assert_called_once()

    def test_tool_dispatcher_concurrent_reads_and_sequential_writes_with_cancel(self):
        with tempfile.TemporaryDirectory() as td:
            audit_logger = AuditLogger(log_path=os.path.join(td, "audit.jsonl"))
            registry = ToolRegistry()
            read_barrier = threading.Barrier(2, timeout=2.0)
            read_cancel_seen = []

            def read_a(tag: str):
                cc = get_active_tool_cancel_check()
                read_cancel_seen.append(cc() if cc else None)
                read_barrier.wait()
                return f"a:{tag}"

            def read_b(tag: str):
                cc = get_active_tool_cancel_check()
                read_cancel_seen.append(cc() if cc else None)
                read_barrier.wait()
                return f"b:{tag}"

            write_state = {"active": 0, "max_active": 0}
            write_lock = threading.Lock()

            def write_op(val: str):
                with write_lock:
                    write_state["active"] += 1
                    write_state["max_active"] = max(write_state["max_active"], write_state["active"])
                time.sleep(0.03)
                with write_lock:
                    write_state["active"] -= 1
                return f"w:{val}"

            for name, fn, cls in (
                ("read_a", read_a, "READ"),
                ("read_b", read_b, "READ"),
                ("write_1", write_op, "WRITE"),
                ("write_2", write_op, "WRITE"),
            ):
                registry.register(
                    name=name,
                    func=fn,
                    description=name,
                    classification=cls,
                    requires_human_approval=False,
                    allowed_roles={"Terminal Orchestrator"},
                    parameters={"tag": str} if cls == "READ" else {"val": str},
                    required_permission="read_market" if cls == "READ" else "write_lessons",
                )

            dispatcher = ToolDispatcher(registry=registry, audit_logger=audit_logger, max_turns=4)
            mock_session = MagicMock()
            mock_session.send_message.side_effect = [
                SimpleNamespace(
                    text="Turn 1 reads",
                    function_calls=[
                        SimpleNamespace(name="read_a", args={"tag": "1"}),
                        SimpleNamespace(name="read_b", args={"tag": "2"}),
                    ],
                ),
                SimpleNamespace(
                    text="Turn 2 writes",
                    function_calls=[
                        SimpleNamespace(name="write_1", args={"val": "x"}),
                        SimpleNamespace(name="write_2", args={"val": "y"}),
                    ],
                ),
                SimpleNamespace(text="Done", function_calls=[]),
            ]

            res = dispatcher.execute_multi_turn_session(
                session=mock_session,
                initial_message="run",
                part_factory=_MockPartFactory,
                agent_role="Terminal Orchestrator",
                cancel_check=lambda: False,
            )

            self.assertIn("Done", res["response"])
            self.assertEqual(read_cancel_seen, [False, False])
            self.assertEqual(write_state["max_active"], 1)

    def test_perform_web_forensic_search_memoization_and_cancel(self):
        with tools._WEB_SEARCH_CACHE_LOCK:
            orig_cache = dict(tools._WEB_SEARCH_CACHE)
            tools._WEB_SEARCH_CACHE.clear()
            tools._WEB_SEARCH_INFLIGHT.clear()

        try:
            mock_runner = MagicMock()
            mock_runner.run_prompt.return_value = AntigravityResponse("Mock catalyst summary")

            with patch("agent_framework.AntigravityAgentRunner", return_value=mock_runner):
                first = tools.perform_web_forensic_search("NVDA catalyst filing")
                second = tools.perform_web_forensic_search("  nvda   CATALYST filing ")

            self.assertEqual(first, "Mock catalyst summary")
            self.assertEqual(second, "Mock catalyst summary")
            self.assertEqual(mock_runner.run_prompt.call_count, 1)

            mock_runner.run_prompt.reset_mock()
            with patch("tool_dispatcher.get_active_tool_cancel_check", return_value=lambda: True), \
                 patch("agent_framework.AntigravityAgentRunner", return_value=mock_runner):
                cancelled_out = tools.perform_web_forensic_search("UNCACHED cancelled query")
            self.assertIn("cancelled", cancelled_out.lower())
            self.assertEqual(mock_runner.run_prompt.call_count, 0)
        finally:
            with tools._WEB_SEARCH_CACHE_LOCK:
                tools._WEB_SEARCH_CACHE.clear()
                tools._WEB_SEARCH_CACHE.update(orig_cache)
                tools._WEB_SEARCH_INFLIGHT.clear()

    def test_market_data_cache_garbage_collect_prunes_all_ticker_dicts(self):
        md_cache = fetch_stocks.MarketDataCache()
        for sym in ("AAPL", "OLD"):
            md_cache.prices[sym] = 100.0
            md_cache.gex[sym] = {"net_gex": 1.0}
            md_cache.qualitative_grounding[sym] = {"summary": "test"}
            md_cache.metric_provenance[sym] = {"QUOTE": {"source": "yfinance"}}
            md_cache.history[sym] = pd.DataFrame({"Close": [1.0]})
            md_cache.technicals[sym] = {"RSI": 50.0}
            md_cache.last_history_fetch_date[sym] = "2026-11-26"

        md_cache.garbage_collect(["AAPL"])

        for d in (
            md_cache.prices,
            md_cache.gex,
            md_cache.qualitative_grounding,
            md_cache.metric_provenance,
            md_cache.history,
            md_cache.technicals,
            md_cache.last_history_fetch_date,
        ):
            self.assertIn("AAPL", d)
            self.assertNotIn("OLD", d)

    def test_sse_log_fanout_delivers_to_multiple_subscribers_and_unsubscribes(self):
        async def _run():
            orig_loop = web_server.main_loop
            web_server.main_loop = asyncio.get_running_loop()
            q1 = web_server.subscribe_log_queue()
            q2 = web_server.subscribe_log_queue()
            try:
                web_server.log_to_queue("[StageB] fanout msg 1")
                await asyncio.sleep(0)
                self.assertEqual(q1.get_nowait(), "[StageB] fanout msg 1")
                self.assertEqual(q2.get_nowait(), "[StageB] fanout msg 1")

                web_server.unsubscribe_log_queue(q1)
                web_server.log_to_queue("[StageB] fanout msg 2")
                await asyncio.sleep(0)
                self.assertTrue(q1.empty())
                self.assertEqual(q2.get_nowait(), "[StageB] fanout msg 2")
            finally:
                web_server.unsubscribe_log_queue(q1)
                web_server.unsubscribe_log_queue(q2)
                web_server.main_loop = orig_loop

        asyncio.run(_run())

    def test_sqlite_adapter_caches_base_ssot_and_single_connection_daemon_snapshot(self):
        repo_root = Path(__file__).resolve().parent.parent
        src_db = repo_root / "context" / "gem.db"
        src_ssot = repo_root / "context" / "ssot.json"

        with tempfile.TemporaryDirectory() as td:
            tmp_db = Path(td) / "gem.db"
            tmp_ssot = Path(td) / "ssot.json"
            shutil.copy2(src_db, tmp_db)
            shutil.copy2(src_ssot, tmp_ssot)

            db_mgr = DatabaseManager(db_path=str(tmp_db), profile=PRIMARY_FINANCIAL_PROFILE)
            adapter = SqlitePersistenceAdapter(db_manager=db_mgr, base_ssot_path=str(tmp_ssot))

            orig_json_load = json.load
            with patch("python.persistence.sqlite_adapter.json.load", wraps=orig_json_load) as spy_load:
                first = adapter.get_ssot_data()
                second = adapter.get_ssot_data()
            self.assertEqual(spy_load.call_count, 1)
            self.assertEqual(first.get("mutable_state", {}).get("portfolio_snapshot"),
                             second.get("mutable_state", {}).get("portfolio_snapshot"))

            orig_connection = db_mgr.connection
            with patch.object(db_mgr, "connection", wraps=orig_connection) as spy_conn:
                snap = adapter.get_daemon_cycle_snapshot()
            self.assertEqual(spy_conn.call_count, 1)
            ssot_slim = snap["ssot_slim"]
            self.assertNotIn("decision_log", ssot_slim)
            self.assertNotIn("trade_lessons", ssot_slim)
            self.assertNotIn("decision_log", ssot_slim.get("mutable_state", {}))
            self.assertNotIn("trade_lessons", ssot_slim.get("mutable_state", {}))
            self.assertIsInstance(snap["trade_lessons"], list)
            self.assertIsInstance(snap["ssot_tickers"], list)
            self.assertIsInstance(snap["held_and_watched"], set)

    def test_intraday_payload_ttl_cache_and_compact_1m_summary(self):
        with fetch_stocks._INTRADAY_CACHE_LOCK:
            orig_cache = dict(fetch_stocks._INTRADAY_CACHE)
            fetch_stocks._INTRADAY_CACHE.clear()

        try:
            idx = pd.date_range("2026-11-26 09:30", periods=6, freq="1min", tz="America/New_York")
            df = pd.DataFrame(
                {
                    "Open": [100.0, 100.5, 101.0, 101.5, 102.0, 102.5],
                    "High": [100.8, 101.2, 101.6, 102.2, 102.8, 103.5],
                    "Low": [99.5, 100.1, 100.7, 101.2, 101.8, 102.2],
                    "Close": [100.4, 100.9, 101.4, 101.9, 102.4, 103.0],
                    "Volume": [1000, 1200, 1100, 1300, 1400, 1500],
                },
                index=idx,
            )
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df

            with patch.object(fetch_stocks.yf, "Ticker", return_value=mock_ticker) as spy_ticker:
                p1 = fetch_stocks._fetch_intraday_payload("AAPL")
                p2 = fetch_stocks._fetch_intraday_payload("aapl")
                summary = fetch_stocks.build_compact_intraday_1m_summary(["AAPL"], max_symbols=4)

            self.assertEqual(spy_ticker.call_count, 1)
            self.assertEqual(p1["symbol"], "AAPL")
            self.assertEqual(len(p1["bars"]), 6)
            self.assertEqual(p1, p2)
            self.assertIn("AAPL", summary)
            entry = summary["AAPL"]
            self.assertNotIn("bars", entry)
            self.assertEqual(entry["bar_count_1m"], 6)
            self.assertEqual(entry["high"], 103.5)
            self.assertEqual(entry["low"], 99.5)
            self.assertEqual(entry["last"], 103.0)
            self.assertIsNotNone(entry["vwap_1m"])
        finally:
            with fetch_stocks._INTRADAY_CACHE_LOCK:
                fetch_stocks._INTRADAY_CACHE.clear()
                fetch_stocks._INTRADAY_CACHE.update(orig_cache)


if __name__ == "__main__":
    unittest.main()
