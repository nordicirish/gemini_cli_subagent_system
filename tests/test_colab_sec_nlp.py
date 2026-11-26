"""
Stage M3 Unit & Integration Tests for Zero-Token GPU SEC/Earnings NLP Worker (#3):
- Deterministic structural SEC clause extraction (424B5 ATM shelf, warrant coverage, going concern)
- Offline FinBERT/scorer aggregation -> strict Schema v2.0 `sec_nlp` validation pass
- Opt-in `use_llm=True` clean fallback when L4 GPU is unavailable
- sec_edgar.fetch_recent_sec_filings() & AnalyticsEngine.evaluate() read-only `sec_nlp` enrichment
"""

from datetime import datetime, timedelta, timezone
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import analytics_engine
import colab_calibration
from colab_workers import sec_nlp_worker
import sec_edgar


class TestSecNlpWorker(unittest.TestCase):
    def test_structural_extractor_flags_dilution_warrants_and_going_concern(self):
        filings = [
            {
                "form": "424B5",
                "filing_date": "2026-11-24",
                "text": (
                    "<html><body><p>Prospectus Supplement for an at-the-market offering program "
                    "up to $75.0 million aggregate offering price of common stock. "
                    "Includes warrant coverage of 50.0% exercisable immediately. "
                    "Note substantial doubt about our ability to continue as a going concern.</p></body></html>"
                ),
            }
        ]
        sig = sec_nlp_worker.extract_sec_structural_signals(filings)
        self.assertTrue(sig["offering_overhang_flag"])
        self.assertTrue(sig["going_concern_flag"])
        self.assertEqual(sig["shelf_remaining_usd_m"], 75.0)
        self.assertEqual(sig["warrant_overhang_pct"], 50.0)
        self.assertGreaterEqual(sig["structural_dilution_risk"], 70.0)
        self.assertGreater(len(sig["evidence_snippets"]), 0)
        self.assertLessEqual(len(sig["evidence_snippets"]), 5)

    def test_run_sec_nlp_job_passes_schema_v2_and_skips_indices(self):
        def _mock_fetcher(sym: str):
            if sym == "UMAC":
                return [
                    {
                        "form": "8-K",
                        "filing_date": "2026-11-20",
                        "text": "Record contract award and backlog growth of 140% with strong gross profit expansion. " * 4,
                    }
                ]
            if sym == "DILU":
                return [
                    {
                        "form": "S-3",
                        "filing_date": "2026-11-22",
                        "text": "Shelf registration statement and sales agreement up to $50 million at-the-market equity offering. " * 4,
                    }
                ]
            return []

        def _mock_scorer(chunks):
            out = []
            for c in chunks:
                if c["form"] == "S-3":
                    out.append({"label": "negative", "score": 0.85})
                else:
                    out.append({"label": "positive", "score": 0.90})
            return out

        payload = sec_nlp_worker.run_sec_nlp_job(
            {"symbols": ["^VIX", "UMAC", "DILU"], "options": {"use_llm": True}},
            filing_fetcher=_mock_fetcher,
            sentiment_scorer=_mock_scorer,
        )
        validated = colab_calibration.validate_sec_nlp_artifact(payload)
        self.assertEqual(validated["schema_version"], "2.0")
        self.assertNotIn("^VIX", validated["tickers"])
        self.assertIn("UMAC", validated["tickers"])
        self.assertIn("DILU", validated["tickers"])
        self.assertGreater(validated["tickers"]["UMAC"]["catalyst_sentiment"], 0.5)
        self.assertTrue(validated["tickers"]["DILU"]["offering_overhang_flag"])
        self.assertGreater(validated["tickers"]["DILU"]["dilution_risk_score"], 60.0)

    def test_sec_edgar_and_analytics_engine_attach_sec_nlp(self):
        with tempfile.TemporaryDirectory() as td:
            gen_utc = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            artifact = {
                "schema_version": "2.0",
                "generated_at_utc": gen_utc,
                "source": "google_colab_mcp",
                "model_used": "ProsusAI/finbert",
                "tickers": {
                    "UMAC": {
                        "dilution_risk_score": 78.0,
                        "catalyst_sentiment": -0.45,
                        "offering_overhang_flag": True,
                        "going_concern_flag": False,
                        "earnings_tone_delta": -0.2,
                        "filings_analyzed": 3,
                        "shelf_remaining_usd_m": 40.0,
                        "warrant_overhang_pct": 12.5,
                        "evidence_snippets": ["[424B5] Active ATM sales agreement up to $40M."],
                    }
                },
            }
            colab_calibration.import_colab_artifact("sec_nlp", artifact, cache_dir=td)

            # 1. sec_edgar attachment
            sec_cache_path = os.path.join(td, "sec_edgar_cache.json")

            def _fake_http(url: str):
                if "company_tickers" in url:
                    return {"0": {"ticker": "UMAC", "cik_str": 1956955}}
                return {
                    "name": "Unusual Machines",
                    "filings": {
                        "recent": {
                            "form": ["8-K"],
                            "filingDate": ["2026-11-20"],
                            "accessionNumber": ["0001-26-000001"],
                            "primaryDocument": ["doc.htm"],
                            "primaryDocDescription": ["8-K"],
                        }
                    },
                }

            res = sec_edgar.fetch_recent_sec_filings(
                "UMAC",
                cache_path=sec_cache_path,
                http_get=_fake_http,
                colab_cache_dir=td,
            )
            self.assertEqual(res["status"], "ok")
            self.assertIn("sec_nlp", res)
            self.assertTrue(res["sec_nlp"]["offering_overhang_flag"])
            self.assertFalse(res["sec_nlp"]["stale"])

            # 2. AnalyticsEngine attachment
            orig_load = colab_calibration.load_colab_artifact
            orig_status = colab_calibration.get_artifact_status
            live_state = {
                "tickers": [
                    {
                        "ticker": "UMAC",
                        "price": 12.0,
                        "regular_close": 11.5,
                        "price_validity": "VALID",
                        "freshness": "CURRENT",
                        "dealer_posture": "LONG_GAMMA",
                        "net_gex_total": 0.1,
                        "rsi": 58.0,
                        "rvol": 1.2,
                        "vwap": 11.8,
                    }
                ]
            }
            ssot = {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "UMAC", "shares": 100, "wac": 10.0}],
                    "watched_tickers": [],
                    "unallocated_cash_eur": 500.0,
                }
            }
            eng = analytics_engine.AnalyticsEngine()
            with patch.object(analytics_engine, "load_colab_artifact", side_effect=lambda at: orig_load(at, cache_dir=td)), \
                 patch.object(analytics_engine, "get_artifact_status", side_effect=lambda at: orig_status(at, cache_dir=td)):
                bundle = eng.evaluate(live_state, ssot=ssot)

            self.assertTrue(bundle["colab_sec_nlp"]["loaded"])
            self.assertEqual(bundle["colab_sec_nlp"]["model_used"], "ProsusAI/finbert")
            self.assertEqual(bundle["positions"][0]["sec_nlp"]["dilution_risk_score"], 78.0)


if __name__ == "__main__":
    unittest.main()
