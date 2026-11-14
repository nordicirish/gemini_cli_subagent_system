"""
JSON-Backed Persistence Adapter for GEM.
Governed by Stage 2 Architecture and Agent Governance Framework.

Preserves exact existing file paths, formats, atomic I/O semantics,
and authorization boundaries for the default runtime:
- context/ssot.json
- context/config.json
- context/trade_lessons.json
- context/decision_log.json
"""

import datetime
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Union

try:
    import atomic_io
except ImportError:
    from python import atomic_io

def atomic_read_json(*args, **kwargs):
    return atomic_io.atomic_read_json(*args, **kwargs)

def atomic_update_json(*args, **kwargs):
    return atomic_io.atomic_update_json(*args, **kwargs)

def atomic_write_json(*args, **kwargs):
    return atomic_io.atomic_write_json(*args, **kwargs)

try:
    from python.tool_registry import authorize_financial_mutation, get_active_caller_role
except ImportError:
    from tool_registry import authorize_financial_mutation, get_active_caller_role

from .contracts import (
    PersistenceAdapter,
    PersistenceError,
    PersistenceIntegrityError,
    PersistenceNotFoundError,
    PersistenceValidationError,
    CANONICAL_SSOT_KEYS,
)


class JsonPersistenceAdapter(PersistenceAdapter):
    """
    Authoritative JSON persistence adapter utilizing atomic read-modify-write file operations.
    Default runtime backend for GEM Stage 2.
    """

    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = base_dir or ""
        self.ssot_path = os.path.join(self._base_dir, "context", "ssot.json") if self._base_dir else "context/ssot.json"
        self.config_path = os.path.join(self._base_dir, "context", "config.json") if self._base_dir else "context/config.json"
        self.trade_lessons_path = os.path.join(self._base_dir, "context", "trade_lessons.json") if self._base_dir else "context/trade_lessons.json"
        self.decision_log_path = os.path.join(self._base_dir, "context", "decision_log.json") if self._base_dir else "context/decision_log.json"

    @property
    def backend_name(self) -> str:
        return "json"

    # -----------------------------------------------------------------------
    # Portfolio State & SSoT Operations
    # -----------------------------------------------------------------------

    def _read_raw_ssot_dict(self) -> Dict[str, Any]:
        """
        Reads and parses the raw SSoT document under concurrency protection.
        - Returns {} if file does not exist (legitimate first-run state).
        - Raises PersistenceIntegrityError if file exists but is empty or malformed JSON.
        - Raises PersistenceIntegrityError if root is not a dict.
        - Raises PersistenceError if I/O or permissions fail.
        """
        if not os.path.exists(self.ssot_path):
            return {}

        lock_path = self.ssot_path + ".lock"
        try:
            with atomic_io.FileLock(lock_path, timeout=5.0):
                if not os.path.exists(self.ssot_path):
                    return {}
                with open(self.ssot_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if not content:
                    raise PersistenceIntegrityError(
                        f"SSoT file exists but is empty or whitespace-only: '{self.ssot_path}'"
                    )
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as jde:
                    raise PersistenceIntegrityError(
                        f"SSoT file contains malformed JSON: '{self.ssot_path}': {jde}"
                    ) from jde
                if not isinstance(data, dict):
                    raise PersistenceIntegrityError(
                        f"SSoT file root must be a JSON object (dict), got {type(data).__name__}"
                    )
                return data
        except (PersistenceIntegrityError, PersistenceValidationError):
            raise
        except (IOError, OSError) as e:
            raise PersistenceError(f"I/O failure reading SSoT file '{self.ssot_path}': {e}") from e
        except Exception as e:
            raise PersistenceError(f"Unexpected error reading SSoT file '{self.ssot_path}': {e}") from e

    def get_ssot_data(self) -> Dict[str, Any]:
        """
        Reads the raw SSoT document.
        Returns {} if file does not exist.
        Raises PersistenceIntegrityError or PersistenceError if file exists but cannot be read/parsed.
        """
        return self._read_raw_ssot_dict()

    def get_portfolio_basket(self) -> Dict[str, Any]:
        """
        Extracts structured portfolio basket holdings, unallocated cash,
        and fallback EUR/USD rate from ssot.json.
        Returns default empty basket if file does not exist.
        Raises PersistenceIntegrityError, PersistenceValidationError, or PersistenceError on failure.
        """
        if not os.path.exists(self.ssot_path):
            return {
                "portfolio": [],
                "unallocated_cash_eur": 0.0,
                "unallocated_cash_usd": 0.0,
                "eurusd_rate": 1.08,
            }

        data = self._read_raw_ssot_dict()
        if not data:
            return {
                "portfolio": [],
                "unallocated_cash_eur": 0.0,
                "unallocated_cash_usd": 0.0,
                "eurusd_rate": 1.08,
            }

        ms = data.get("mutable_state", {})
        if ms is not None and not isinstance(ms, dict):
            raise PersistenceIntegrityError("mutable_state in SSoT must be a dictionary")
        ms = ms or {}

        ep = data.get("EXECUTION_PAYLOAD", {})
        if ep is not None and not isinstance(ep, dict):
            raise PersistenceIntegrityError("EXECUTION_PAYLOAD in SSoT must be a dictionary")
        ep = ep or {}

        state_ctx = data.get("state_context", {})
        if state_ctx is not None and not isinstance(state_ctx, dict):
            raise PersistenceIntegrityError("state_context in SSoT must be a dictionary")
        state_ctx = state_ctx or {}

        raw_portfolio = (
            ms.get("portfolio_snapshot")
            if ms.get("portfolio_snapshot") is not None
            else data.get("portfolio_snapshot")
            if data.get("portfolio_snapshot") is not None
            else ep.get("portfolio_snapshot")
            if ep.get("portfolio_snapshot") is not None
            else []
        )

        if not isinstance(raw_portfolio, list):
            raise PersistenceValidationError(
                f"portfolio_snapshot must be a list, got {type(raw_portfolio).__name__}"
            )

        portfolio = []
        for i, item in enumerate(raw_portfolio):
            if not isinstance(item, dict):
                raise PersistenceValidationError(f"Portfolio position item {i} must be a dictionary")
            ticker = item.get("ticker")
            if not ticker or not isinstance(ticker, str) or not ticker.strip():
                raise PersistenceValidationError(f"Portfolio position item {i} missing valid ticker")
            try:
                shares = float(item.get("shares", 0.0))
                if shares < 0:
                    raise PersistenceValidationError(f"Negative shares for position {ticker}: {shares}")
                wac = float(item.get("wac", 0.0))
                if wac < 0:
                    raise PersistenceValidationError(f"Negative wac for position {ticker}: {wac}")
            except (ValueError, TypeError) as e:
                raise PersistenceValidationError(f"Invalid numeric values in position {ticker}: {e}") from e

            portfolio.append({
                "ticker": str(ticker).strip().upper(),
                "shares": shares,
                "wac": wac,
            })

        try:
            raw_cash_eur = (
                ms.get("unallocated_cash_eur") if ms.get("unallocated_cash_eur") is not None else
                data.get("unallocated_cash_eur") if data.get("unallocated_cash_eur") is not None else
                ep.get("unallocated_cash_eur") if ep.get("unallocated_cash_eur") is not None else
                state_ctx.get("unallocated_cash_eur", 0.0)
            )
            raw_cash_usd = (
                ms.get("unallocated_cash_usd") if ms.get("unallocated_cash_usd") is not None else
                data.get("unallocated_cash_usd") if data.get("unallocated_cash_usd") is not None else
                ep.get("unallocated_cash_usd") if ep.get("unallocated_cash_usd") is not None else
                state_ctx.get("unallocated_cash_usd", 0.0)
            )
            cash_eur = float(raw_cash_eur if raw_cash_eur is not None else 0.0)
            cash_usd = float(raw_cash_usd if raw_cash_usd is not None else 0.0)
            rate = float(data.get("exchange_rate", 1.08)) if data.get("exchange_rate") is not None else 1.08
        except (ValueError, TypeError) as e:
            raise PersistenceValidationError(f"Invalid cash or exchange rate values in SSoT: {e}") from e

        return {
            "portfolio": portfolio,
            "unallocated_cash_eur": cash_eur,
            "unallocated_cash_usd": cash_usd,
            "eurusd_rate": rate,
        }

    def save_portfolio_basket(
        self,
        portfolio: List[Dict[str, Any]],
        unallocated_cash_eur: float,
        unallocated_cash_usd: float = 0.0,
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Saves updated portfolio holdings and cash balances into ssot.json atomically.
        Enforces authorize_financial_mutation before disk modification.
        """
        payload = {
            "portfolio": portfolio,
            "unallocated_cash_eur": unallocated_cash_eur,
            "unallocated_cash_usd": unallocated_cash_usd,
        }
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=self.ssot_path,
            payload=payload,
            caller_role=caller_role,
        )

        new_snapshot = []
        for item in portfolio:
            shares = float(item.get("shares", 0.0))
            if shares > 0.0:
                new_snapshot.append({
                    "ticker": str(item["ticker"]).strip().upper(),
                    "shares": shares,
                    "wac": float(item.get("wac", 0.0)),
                })

        eur_cash = float(unallocated_cash_eur)
        usd_cash = float(unallocated_cash_usd) if unallocated_cash_usd > 0.0 else round(eur_cash * 1.08, 2)

        def _update_basket_ssot(data: Any) -> Dict[str, Any]:
            if not isinstance(data, dict):
                data = {}
            data["portfolio_snapshot"] = new_snapshot
            data["unallocated_cash_eur"] = eur_cash
            data["unallocated_cash_usd"] = usd_cash

            if "mutable_state" in data and isinstance(data["mutable_state"], dict):
                data["mutable_state"]["portfolio_snapshot"] = new_snapshot
                data["mutable_state"]["unallocated_cash_eur"] = eur_cash
                data["mutable_state"]["unallocated_cash_usd"] = usd_cash

            return data

        updated_data = atomic_update_json(self.ssot_path, _update_basket_ssot, default_factory=dict)
        return {
            "status": "success",
            "portfolio": new_snapshot,
            "unallocated_cash_eur": eur_cash,
            "unallocated_cash_usd": usd_cash,
        }

    def update_ssot_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Processes and merges an execution payload into ssot.json,
        extracts embedded trade lessons to trade_lessons.json, and logs decisions.
        """
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=self.ssot_path,
            payload=payload,
            caller_role=caller_role,
        )

        parsed_data = self._parse_payload_input(payload)
        if not isinstance(parsed_data, dict):
            raise PersistenceValidationError("Payload must resolve to a dictionary.")

        # Intercept decision log early if present
        try:
            self.append_decision_log_from_payload(parsed_data, caller_role=caller_role)
        except Exception:
            pass

        # Extract lessons from payload
        extracted_lessons = self._extract_lessons(parsed_data)

        # Merge portfolio and mutable state into ssot.json
        def _merge_ssot_state(existing_ssot: Any) -> Dict[str, Any]:
            if not isinstance(existing_ssot, dict):
                existing_ssot = {}

            local_payload = json.loads(json.dumps(parsed_data))
            has_layer_model = "mutable_state" in existing_ssot

            # Alias mapping
            self._map_aliases(local_payload)
            if isinstance(local_payload.get("mutable_state"), dict):
                self._map_aliases(local_payload["mutable_state"])
            if isinstance(local_payload.get("EXECUTION_PAYLOAD"), dict):
                self._map_aliases(local_payload["EXECUTION_PAYLOAD"])

            merged_portfolio = None
            if "portfolio_snapshot" in local_payload:
                merged_portfolio = local_payload.pop("portfolio_snapshot")
            elif "mutable_state" in local_payload and isinstance(local_payload["mutable_state"], dict) and "portfolio_snapshot" in local_payload["mutable_state"]:
                merged_portfolio = local_payload["mutable_state"].pop("portfolio_snapshot")

            if existing_ssot:
                if merged_portfolio is not None:
                    merged_ssot = self._deep_merge(existing_ssot, local_payload)
                    if has_layer_model:
                        if "mutable_state" not in merged_ssot or not isinstance(merged_ssot["mutable_state"], dict):
                            merged_ssot["mutable_state"] = {}
                        merged_ssot["mutable_state"]["portfolio_snapshot"] = merged_portfolio
                    else:
                        merged_ssot["portfolio_snapshot"] = merged_portfolio
                else:
                    merged_ssot = self._deep_merge(existing_ssot, local_payload)
            else:
                merged_ssot = local_payload
                if merged_portfolio is not None:
                    if has_layer_model:
                        if "mutable_state" not in merged_ssot or not isinstance(merged_ssot["mutable_state"], dict):
                            merged_ssot["mutable_state"] = {}
                        merged_ssot["mutable_state"]["portfolio_snapshot"] = merged_portfolio
                    else:
                        merged_ssot["portfolio_snapshot"] = merged_portfolio

            # Strip lesson keys from SSoT
            for tl_key in ("trade_lessons", "new_trade_lessons", "compressed_trade_lessons", "rule_mutations", "trade_lessons_revision"):
                merged_ssot.pop(tl_key, None)
                if "mutable_state" in merged_ssot and isinstance(merged_ssot["mutable_state"], dict):
                    merged_ssot["mutable_state"].pop(tl_key, None)
                if "EXECUTION_PAYLOAD" in merged_ssot and isinstance(merged_ssot["EXECUTION_PAYLOAD"], dict):
                    merged_ssot["EXECUTION_PAYLOAD"].pop(tl_key, None)

            # Prune non-canonical top-level keys
            non_canonical = [k for k in merged_ssot if k not in CANONICAL_SSOT_KEYS]
            for k in non_canonical:
                merged_ssot.pop(k, None)

            return merged_ssot

        result = atomic_update_json(self.ssot_path, _merge_ssot_state, default_factory=dict)

        # Route extracted trade lessons to trade_lessons.json
        if extracted_lessons:
            try:
                self.save_trade_lessons(extracted_lessons, caller_role=caller_role)
            except Exception:
                pass

        return {"status": "success", "ssot": result}

    def get_ssot_tickers(self) -> List[str]:
        """Extracts all uppercase active tickers from SSoT, config watchlist, and scouts."""
        combined: List[str] = []
        try:
            if os.path.exists(self.ssot_path):
                ssot = atomic_read_json(self.ssot_path, default={})
                ms = ssot.get("mutable_state", {}) if isinstance(ssot.get("mutable_state"), dict) else {}
                ep = ssot.get("EXECUTION_PAYLOAD", {}) if isinstance(ssot.get("EXECUTION_PAYLOAD"), dict) else {}

                raw_portfolio = (
                    ms.get("portfolio_snapshot") or
                    ssot.get("portfolio_snapshot") or
                    ep.get("portfolio_snapshot") or
                    []
                )
                for item in raw_portfolio:
                    if isinstance(item, dict) and item.get("ticker"):
                        t = str(item["ticker"]).strip().upper()
                        if t and t not in combined:
                            combined.append(t)

                watched = (
                    ms.get("watched_tickers") or
                    ssot.get("watched_tickers") or
                    ep.get("watched_tickers") or
                    []
                )
                for w in watched:
                    if isinstance(w, str) and w.strip():
                        w_clean = w.strip().upper()
                        if w_clean not in combined:
                            combined.append(w_clean)

            # Check config.json
            if os.path.exists(self.config_path):
                cfg = atomic_read_json(self.config_path, default={})
                for w in cfg.get("WATCHLIST", []):
                    if isinstance(w, str) and w.strip():
                        w_clean = w.strip().upper()
                        if w_clean not in combined:
                            combined.append(w_clean)
        except Exception:
            pass
        return combined

    def reconcile_ssot_format(self) -> None:
        """Ensures that mutable_state is populated and coherent in ssot.json."""
        if not os.path.exists(self.ssot_path):
            return

        def _reconcile(data: Any) -> Dict[str, Any]:
            if not isinstance(data, dict):
                return {}
            if "mutable_state" not in data or not isinstance(data["mutable_state"], dict):
                data["mutable_state"] = {
                    "portfolio_snapshot": data.get("portfolio_snapshot", []),
                    "watched_tickers": data.get("watched_tickers", []),
                    "unallocated_cash_eur": data.get("unallocated_cash_eur", 0.0),
                    "unallocated_cash_usd": data.get("unallocated_cash_usd", 0.0),
                }
            return data

        atomic_update_json(self.ssot_path, _reconcile, default_factory=dict)

    # -----------------------------------------------------------------------
    # Watchlist & Scout Operations
    # -----------------------------------------------------------------------

    def get_watchlist(self) -> List[str]:
        """Reads active watchlist from config.json or ssot.json."""
        watchlist: List[str] = []
        if os.path.exists(self.config_path):
            try:
                cfg = atomic_read_json(self.config_path, default={})
                watchlist = cfg.get("WATCHLIST", [])
            except Exception:
                watchlist = []

        if not watchlist and os.path.exists(self.ssot_path):
            try:
                data = atomic_read_json(self.ssot_path, default={})
                ms = data.get("mutable_state", {}) if isinstance(data.get("mutable_state"), dict) else data
                watchlist = ms.get("watched_tickers", data.get("watched_tickers", []))
            except Exception:
                watchlist = []

        return [w.strip().upper() for w in watchlist if isinstance(w, str) and w.strip()]

    def save_watchlist(
        self,
        watchlist: Union[List[str], Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> List[str]:
        """
        Saves updated watchlist to both config.json and ssot.json atomically.
        Enforces authorize_financial_mutation.
        """
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=self.ssot_path,
            payload=watchlist,
            caller_role=caller_role,
        )

        if isinstance(watchlist, dict):
            raw_list = watchlist.get("watchlist", [])
        elif isinstance(watchlist, list):
            raw_list = watchlist
        else:
            raw_list = []

        clean_list = [w.strip().upper() for w in raw_list if isinstance(w, str) and w.strip()]

        # 1. Update config.json
        if os.path.exists(self.config_path):
            def _update_cfg_wl(cfg: Any) -> Dict[str, Any]:
                if not isinstance(cfg, dict):
                    cfg = {}
                cfg["WATCHLIST"] = clean_list
                return cfg
            atomic_update_json(self.config_path, _update_cfg_wl, default_factory=dict)

        # 2. Update ssot.json
        if os.path.exists(self.ssot_path):
            def _update_ssot_wl(data: Any) -> Dict[str, Any]:
                if not isinstance(data, dict):
                    data = {}
                data["watched_tickers"] = clean_list
                if "mutable_state" in data and isinstance(data["mutable_state"], dict):
                    data["mutable_state"]["watched_tickers"] = clean_list
                return data
            atomic_update_json(self.ssot_path, _update_ssot_wl, default_factory=dict)

        return clean_list

    def get_scout_categories(self) -> Dict[str, Any]:
        """Reads scout_categories from ssot.json."""
        if not os.path.exists(self.ssot_path):
            return {}
        try:
            data = atomic_read_json(self.ssot_path, default={})
            ms = data.get("mutable_state", {}) if isinstance(data.get("mutable_state"), dict) else data
            return ms.get("scout_categories", data.get("scout_categories", {}))
        except Exception:
            return {}

    def save_scout_categories(
        self,
        categories: Union[Dict[str, Any], List[Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Saves scout_categories into ssot.json atomically."""
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=self.ssot_path,
            payload=categories,
            caller_role=caller_role,
        )

        def _update_scouts(data: Any) -> Dict[str, Any]:
            if not isinstance(data, dict):
                data = {}
            data["scout_categories"] = categories
            if "mutable_state" in data and isinstance(data["mutable_state"], dict):
                data["mutable_state"]["scout_categories"] = categories
            return data

        return atomic_update_json(self.ssot_path, _update_scouts, default_factory=dict)

    # -----------------------------------------------------------------------
    # Trade Lessons Operations
    # -----------------------------------------------------------------------

    def get_trade_lessons(self) -> List[Dict[str, Any]]:
        """Reads persisted trade lessons from trade_lessons.json."""
        if not os.path.exists(self.trade_lessons_path):
            return []
        try:
            data = atomic_read_json(self.trade_lessons_path, default=[])
            if isinstance(data, dict) and "trade_lessons" in data:
                return list(data["trade_lessons"])
            elif isinstance(data, list):
                return list(data)
            return []
        except Exception as e:
            raise PersistenceError(f"Failed to read trade lessons from JSON: {e}") from e

    def save_trade_lessons(
        self,
        lessons: Union[List[Dict[str, Any]], Dict[str, Any], str],
        caller_role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Appends or updates trade lessons in trade_lessons.json atomically."""
        authorize_financial_mutation(
            operation_name="update_trade_lessons",
            target_resource=self.trade_lessons_path,
            payload=lessons,
            caller_role=caller_role,
        )

        if isinstance(lessons, str):
            try:
                new_data = json.loads(lessons)
            except Exception:
                new_data = [{"rule": lessons}]
        else:
            new_data = lessons

        def _apply_lessons_update(file_content: Any) -> Any:
            current_data: List[Dict[str, Any]] = []
            is_dict_format = False

            if isinstance(file_content, dict) and "trade_lessons" in file_content:
                current_data = list(file_content["trade_lessons"])
                is_dict_format = True
            elif isinstance(file_content, list):
                current_data = list(file_content)

            incoming_list: List[Dict[str, Any]] = []
            if isinstance(new_data, list):
                incoming_list = new_data
            elif isinstance(new_data, dict) and "trade_lessons" in new_data:
                incoming_list = new_data["trade_lessons"]
            elif isinstance(new_data, dict):
                incoming_list = [new_data]
            else:
                incoming_list = [{"rule": str(new_data)}]

            for item in incoming_list:
                current_data.append(item)

            # Re-index items if appropriate
            for idx, item in enumerate(current_data):
                if isinstance(item, dict) and "id" not in item:
                    item["id"] = idx + 1

            return {"trade_lessons": current_data} if is_dict_format else current_data

        result = atomic_update_json(self.trade_lessons_path, _apply_lessons_update, default_factory=list)
        return result.get("trade_lessons", result) if isinstance(result, dict) else result

    # -----------------------------------------------------------------------
    # Decision Log Operations
    # -----------------------------------------------------------------------

    def get_decision_log(self) -> List[Dict[str, Any]]:
        """Reads decision records from decision_log.json."""
        if not os.path.exists(self.decision_log_path):
            return []
        try:
            data = atomic_read_json(self.decision_log_path, default=[])
            if isinstance(data, dict) and "decision_log" in data:
                return list(data["decision_log"])
            elif isinstance(data, list):
                return list(data)
            return []
        except Exception as e:
            raise PersistenceError(f"Failed to read decision log from JSON: {e}") from e

    def append_decision_log(
        self,
        entry: Dict[str, Any],
        prepend: bool = False,
        caller_role: Optional[str] = None,
    ) -> None:
        """Appends (or prepends) an entry to decision_log.json atomically with 300 retention cap."""
        authorize_financial_mutation(
            operation_name="update_decision_log",
            target_resource=self.decision_log_path,
            payload=entry,
            caller_role=caller_role,
        )

        def _update_log(existing: Any) -> Any:
            is_dict = False
            if isinstance(existing, dict) and "decision_log" in existing:
                log_data = list(existing["decision_log"])
                is_dict = True
            elif isinstance(existing, list):
                log_data = list(existing)
            else:
                log_data = []

            if prepend:
                log_data.insert(0, entry)
            else:
                log_data.append(entry)

            if len(log_data) > 300:
                log_data = log_data[:300] if prepend else log_data[-300:]

            return {"decision_log": log_data} if is_dict else log_data

        atomic_update_json(self.decision_log_path, _update_log, default_factory=dict)

    def append_decision_log_from_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> bool:
        """Extracts turn decisions from incoming council payload and logs them."""
        parsed = self._parse_payload_input(payload)
        if not isinstance(parsed, dict):
            return False

        incoming_portfolio = self._find_key_recursive(parsed, "portfolio_snapshot")
        if not (incoming_portfolio and isinstance(incoming_portfolio, list)):
            return False

        ts = (
            self._find_key_recursive(parsed, "timestamp") or
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        )
        trigger = self._find_key_recursive(parsed, "trigger_context") or "ROUTINE_OUTPUT"

        market_context = {
            "regime": self._find_key_recursive(parsed, "regime") or self._find_key_recursive(parsed, "risk_regime"),
            "vix_guard": self._find_key_recursive(parsed, "vix_guard"),
            "portfolio_value_eur": (
                self._find_key_recursive(parsed, "portfolio_total_value_eur") or
                self._find_key_recursive(parsed, "total_liquidity_eur")
            ),
            "unallocated_cash_eur": (
                self._find_key_recursive(parsed, "unallocated_cash_eur") or
                self._find_key_recursive(parsed, "remaining_cash_eur")
            ),
        }
        council_debate = self._find_key_recursive(parsed, "council_debate")

        decisions = []
        for item in incoming_portfolio:
            if isinstance(item, dict):
                ticker = item.get("ticker")
                trade_state = item.get("trade_state", item.get("action"))
                scrutiny_audit = item.get("scrutiny_audit")
                price = item.get("price") or item.get("current_price") or item.get("price_at_eval")

                if ticker and (trade_state or scrutiny_audit):
                    decisions.append({
                        "ticker": ticker,
                        "timestamp": ts,
                        "trigger_context": trigger,
                        "price_at_eval": price,
                        "trade_state": trade_state,
                        "scrutiny_audit": scrutiny_audit,
                    })

        if not decisions:
            return False

        turn_log = {
            "timestamp": ts,
            "trigger_context": trigger,
            "market_context": market_context,
            "council_debate": council_debate,
            "decisions": decisions,
        }

        self.append_decision_log(turn_log, prepend=False, caller_role=caller_role)
        return True

    def clear_decision_log(self, caller_role: Optional[str] = None) -> None:
        """Wipes decision_log.json to empty list."""
        authorize_financial_mutation(
            operation_name="update_decision_log",
            target_resource=self.decision_log_path,
            payload={"action": "clear"},
            caller_role=caller_role,
        )
        atomic_write_json(self.decision_log_path, {"decision_log": []})

    def close(self) -> None:
        """No-op for file-backed JSON adapter."""
        pass

    # -----------------------------------------------------------------------
    # Internal Helpers
    # -----------------------------------------------------------------------

    def _parse_payload_input(self, payload: Union[str, Dict[str, Any]]) -> Any:
        if isinstance(payload, dict):
            return payload
        if not isinstance(payload, str):
            return {}

        clean_str = payload.strip()
        # Extract markdown json fence if present
        if "```" in clean_str:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", clean_str, re.IGNORECASE | re.DOTALL)
            if match:
                clean_str = match.group(1).strip()

        # Clean citation markers
        clean_str = re.sub(r'\[cite_start\]', '', clean_str)
        clean_str = re.sub(r'\[cite:\s*\d+(?:,\s*\d+)*\]', '', clean_str)
        clean_str = clean_str.strip()

        # Find outer braces if surrounded by text
        if not (clean_str.startswith("{") or clean_str.startswith("[")):
            sb = clean_str.find("{")
            eb = clean_str.rfind("}")
            if sb != -1 and eb != -1 and eb > sb:
                clean_str = clean_str[sb:eb + 1]

        try:
            return json.loads(clean_str)
        except Exception:
            return {}

    def _map_aliases(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        if "portfolio" in data and "portfolio_snapshot" not in data:
            data["portfolio_snapshot"] = data.pop("portfolio")
        if "terminal_state" in data and "state_context" not in data:
            data["state_context"] = data.pop("terminal_state")
        if "forensic_alerts" in data and "forensic_intelligence" not in data:
            data["forensic_intelligence"] = data.pop("forensic_alerts")

    def _extract_lessons(self, data: Dict[str, Any]) -> List[Any]:
        extracted: List[Any] = []
        for key in ("trade_lessons", "new_trade_lessons", "compressed_trade_lessons"):
            val = data.pop(key, None)
            if val is None and "mutable_state" in data and isinstance(data["mutable_state"], dict):
                val = data["mutable_state"].pop(key, None)
            if val is None and "EXECUTION_PAYLOAD" in data and isinstance(data["EXECUTION_PAYLOAD"], dict):
                val = data["EXECUTION_PAYLOAD"].pop(key, None)

            if isinstance(val, list):
                extracted.extend(val)
            elif isinstance(val, dict):
                extracted.append(val)
        return extracted

    def _deep_merge(self, base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
        merged = base.copy()
        for k, v in update.items():
            if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                merged[k] = self._deep_merge(merged[k], v)
            else:
                merged[k] = v
        return merged

    def _find_key_recursive(self, data: Any, target_key: str) -> Any:
        if not isinstance(data, dict):
            if isinstance(data, list):
                for item in data:
                    res = self._find_key_recursive(item, target_key)
                    if res is not None:
                        return res
            return None
        if target_key in data:
            return data[target_key]
        for k in ("EXECUTION_PAYLOAD", "mutable_state", "state_context", "payload", "post_execution_state"):
            if k in data:
                res = self._find_key_recursive(data[k], target_key)
                if res is not None:
                    return res
        for val in data.values():
            if isinstance(val, (dict, list)):
                res = self._find_key_recursive(val, target_key)
                if res is not None:
                    return res
        return None

