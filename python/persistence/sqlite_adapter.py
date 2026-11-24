"""
SQLite-Backed Persistence Adapter for GEM.
Governed by Stage 2 Architecture and Agent Governance Framework.

Integrates application persistence operations with existing SQLite infrastructure:
- python/database/manager.py (DatabaseManager)
- python/database/repositories/unit_of_work.py (UnitOfWork)
- python/database/repositories/financial.py (CashRepository, PositionRepository, WatchlistRepository)
- python/database/repositories/governance.py (ScoutCategoryRepository, TradeLessonRepository, DecisionLogRepository)

Preserves exact UnitOfWork atomicity, transaction poisoning semantics,
and fail-closed authorization boundaries without modifying production databases.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

try:
    from python.database.manager import DatabaseManager
    from python.database.repositories.unit_of_work import UnitOfWork
    from python.database.repositories.base import (
        RepositoryError,
        ValidationError,
        TransactionIntegrityError,
        decimal_to_canonical_str,
        validate_timestamp_utc,
    )
    from python.database.projections import SSoTProjectionGenerator
    from python.tool_registry import authorize_financial_mutation, get_active_caller_role
except ImportError:
    from database.manager import DatabaseManager
    from database.repositories.unit_of_work import UnitOfWork
    from database.repositories.base import (
        RepositoryError,
        ValidationError,
        TransactionIntegrityError,
        decimal_to_canonical_str,
        validate_timestamp_utc,
    )
    from database.projections import SSoTProjectionGenerator
    from tool_registry import authorize_financial_mutation, get_active_caller_role

from .contracts import (
    PersistenceAdapter,
    PersistenceValidationError,
    PersistenceIntegrityError,
    PersistenceError,
    format_financial_decimal,
    validate_financial_decimal,
    validate_finite_float,
)

# Bare import first: fx_rates holds process-wide quote state, so it must resolve to the same
# module instance the web server's refresher updates (not a separate python.fx_rates copy).
try:
    from fx_rates import derive_usd_cash, get_eurusd
except ImportError:
    from python.fx_rates import derive_usd_cash, get_eurusd


_UNSET = object()


class SqlitePersistenceAdapter(PersistenceAdapter):
    """
    SQLite persistence adapter utilizing existing UnitOfWork and repository infrastructure.
    Used in controlled integration and automated testing environments.
    """

    def __init__(self, db_manager: DatabaseManager, base_ssot_path: Union[str, None, object] = _UNSET):
        if db_manager is None:
            raise ValueError("db_manager cannot be None")
        self._db_manager = db_manager
        if base_ssot_path is _UNSET:
            self._base_ssot_path = "context/ssot.json"
        elif base_ssot_path is None:
            self._base_ssot_path = None
        else:
            self._base_ssot_path = str(base_ssot_path)

    @property
    def backend_name(self) -> str:
        return "sqlite"

    @property
    def db_manager(self) -> DatabaseManager:
        return self._db_manager

    # -----------------------------------------------------------------------
    # Portfolio State & SSoT Operations
    # -----------------------------------------------------------------------

    def get_ssot_data(self) -> Dict[str, Any]:
        """Projects current SQLite state into a canonical SSoT dictionary using SSoTProjectionGenerator."""
        base_doc = None
        if self._base_ssot_path is not None:
            try:
                with open(self._base_ssot_path, "r", encoding="utf-8") as source:
                    base_doc = json.load(source)

                if not isinstance(base_doc, dict):
                    raise PersistenceValidationError(
                        f"Base SSoT document at '{self._base_ssot_path}' must have a dictionary root."
                    )
            except (OSError, json.JSONDecodeError, UnicodeError, PersistenceValidationError) as exc:
                raise PersistenceIntegrityError(
                    f"Cannot generate canonical SSoT projection: base document at '{self._base_ssot_path}' is unavailable or invalid: {exc}"
                ) from exc

        try:
            with self._db_manager.connection() as conn:
                generator = SSoTProjectionGenerator(self._db_manager)
                return generator.generate(conn, base_document=base_doc)
        except (PersistenceValidationError, PersistenceIntegrityError):
            raise
        except Exception as e:
            raise PersistenceError(f"Failed to retrieve canonical SSoT projection from SQLite: {e}") from e

    def _normalize_portfolio_items(self, portfolio: Any) -> Dict[str, Dict[str, Decimal]]:
        if not isinstance(portfolio, list):
            raise PersistenceValidationError("Portfolio must be a list of position dictionaries")

        new_map: Dict[str, Dict[str, Decimal]] = {}
        for item in portfolio:
            if not isinstance(item, dict):
                raise PersistenceValidationError("Portfolio item must be a dictionary")
            t_raw = item.get("ticker", "")
            if not isinstance(t_raw, str) or not t_raw.strip():
                raise PersistenceValidationError("Portfolio item missing ticker")
            t = t_raw.strip().upper()
            shares_val = item["shares"] if "shares" in item else Decimal("0")
            wac_val = item["wac"] if "wac" in item else Decimal("0")
            shares_dec = validate_financial_decimal(
                shares_val, f"shares for {t}", max_scale=4, allow_zero=True, non_negative=True
            )
            wac_dec = validate_financial_decimal(
                wac_val, f"wac for {t}", max_scale=4, allow_zero=True, non_negative=True
            )
            if shares_dec > Decimal("0"):
                if wac_dec == Decimal("0"):
                    raise PersistenceValidationError(
                        f"Cost basis (WAC) must be greater than 0 for active position {t}"
                    )
                new_map[t] = {
                    "shares": shares_dec,
                    "wac": wac_dec,
                }
        return new_map

    def _reconcile_portfolio_and_cash_in_uow(
        self,
        uow: UnitOfWork,
        new_map: Optional[Dict[str, Dict[str, Decimal]]],
        eur_dec: Optional[Decimal],
        usd_dec: Optional[Decimal],
        caller_role: Optional[str] = None,
    ) -> None:
        if eur_dec is not None:
            uow.cash.set_cash_balance(
                account_id="PRIMARY",
                currency="EUR",
                amount=eur_dec,
                caller_role=caller_role,
            )

        if usd_dec is not None:
            uow.cash.set_cash_balance(
                account_id="PRIMARY",
                currency="USD",
                amount=usd_dec,
                caller_role=caller_role,
            )

        if new_map is not None:
            existing_positions = uow.positions.list_positions("PRIMARY")
            existing_map = {p.ticker: p for p in existing_positions}

            for t in existing_map:
                if t not in new_map:
                    uow.positions.delete_position("PRIMARY", t, caller_role=caller_role)

            for t, data in new_map.items():
                uow.positions.upsert_position(
                    account_id="PRIMARY",
                    ticker=t,
                    shares=data["shares"],
                    wac_eur=data["wac"],
                    caller_role=caller_role,
                )

    @staticmethod
    def _resolve_usd_cash(eur_dec: Decimal, usd_dec: Optional[Decimal]) -> Optional[Decimal]:
        """
        Explicit positive USD cash is kept. Otherwise USD is the EUR equivalent at the live
        EUR/USD rate (cents, ROUND_HALF_UP). Returns None (leave stored USD unchanged) when EUR
        cash is positive but no fresh rate exists; zero EUR cash maps to zero USD.
        """
        if usd_dec is not None and (usd_dec > Decimal("0") or eur_dec == Decimal("0")):
            return usd_dec
        if eur_dec == Decimal("0"):
            return Decimal("0.00")
        quote = get_eurusd()
        return derive_usd_cash(eur_dec, quote) if quote else None

    def _parse_payload_input(self, payload: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            clean_str = payload.strip()
            if "```" in clean_str:
                m = re.search(r"```(?:json)?\s*(.*?)\s*```", clean_str, re.IGNORECASE | re.DOTALL)
                if m:
                    clean_str = m.group(1).strip()
            clean_str = re.sub(r"\[cite_start\]", "", clean_str)
            clean_str = re.sub(r"\[cite:\s*\d+(?:,\s*\d+)*\]", "", clean_str).strip()
            if not (clean_str.startswith("{") or clean_str.startswith("[")):
                sb = clean_str.find("{")
                eb = clean_str.rfind("}")
                if sb != -1 and eb != -1 and eb > sb:
                    clean_str = clean_str[sb : eb + 1]
            try:
                parsed = json.loads(clean_str, parse_float=Decimal)
            except json.JSONDecodeError as jde:
                raise PersistenceValidationError(f"Invalid JSON payload: {jde}") from jde
            if not isinstance(parsed, dict):
                raise PersistenceValidationError("Payload must resolve to a dictionary.")
            return parsed
        raise PersistenceValidationError(f"Unsupported payload type: {type(payload).__name__}")

    def _extract_lessons_from_payload(self, data: Dict[str, Any]) -> List[Any]:
        extracted: List[Any] = []
        ms = data.get("mutable_state") if isinstance(data.get("mutable_state"), dict) else {}
        ep = data.get("EXECUTION_PAYLOAD") if isinstance(data.get("EXECUTION_PAYLOAD"), dict) else {}
        for scope in (data, ms, ep):
            for key in ("trade_lessons", "new_trade_lessons", "compressed_trade_lessons"):
                if key not in scope:
                    continue
                val = scope.get(key)
                if val is None:
                    continue
                if isinstance(val, list):
                    extracted.extend(val)
                elif isinstance(val, (dict, str)):
                    extracted.append(val)
                else:
                    raise PersistenceValidationError(
                        f"Invalid trade lessons structure for key '{key}': got {type(val).__name__}"
                    )
        return extracted

    def _save_lessons_in_uow(
        self,
        uow: UnitOfWork,
        raw_lessons: List[Any],
        caller_role: Optional[str] = None,
    ) -> None:
        for idx, item in enumerate(raw_lessons):
            if isinstance(item, str):
                code = f"L-{idx + 1}"
                directive = item
                category = "RISK"
                status = "ACTIVE"
            elif isinstance(item, dict):
                rule_text = item.get("rule", item.get("directive_text", ""))
                if not isinstance(rule_text, str):
                    raise PersistenceValidationError("Trade lesson rule/directive_text must be a string")
                if ":" in rule_text:
                    parts = rule_text.split(":", 1)
                    code = parts[0].strip()
                    directive = parts[1].strip()
                else:
                    code = item.get("rule_code", f"L-{idx + 1}")
                    directive = rule_text
                if not isinstance(directive, str) or not directive.strip():
                    raise PersistenceValidationError(f"Trade lesson at index {idx} has empty directive text")
                category = str(item.get("category", "RISK"))
                status = str(item.get("status", "ACTIVE"))
            else:
                raise PersistenceValidationError(
                    f"Unsupported trade lesson entry type at index {idx}: {type(item).__name__}"
                )

            clean_code = re.sub(r"[^A-Za-z0-9_-]", "", str(code)).upper() or f"L-{idx + 1}"
            uow.trade_lessons.upsert_lesson(
                rule_code=clean_code,
                directive_text=directive,
                category=category,
                status=status,
                caller_role=caller_role,
            )

    def _extract_decision_params_from_payload(
        self,
        data: Dict[str, Any],
        portfolio_source: Any = None,
    ) -> Optional[Dict[str, Any]]:
        ms = data.get("mutable_state") if isinstance(data.get("mutable_state"), dict) else {}
        ep = data.get("EXECUTION_PAYLOAD") if isinstance(data.get("EXECUTION_PAYLOAD"), dict) else {}

        explicit_entry = data.get("decision_log_entry") or ms.get("decision_log_entry") or ep.get("decision_log_entry")
        if explicit_entry is not None and not isinstance(explicit_entry, dict):
            raise PersistenceValidationError("decision_log_entry must be a dictionary")

        has_portfolio_decision = isinstance(portfolio_source, list) and any(
            isinstance(item, dict) and (item.get("trade_state") or item.get("action") or item.get("scrutiny_audit"))
            for item in portfolio_source
        )
        decision_keys = (
            "decision",
            "council_decision",
            "council_debate",
            "decisions",
            "recommendation",
            "final_decision",
            "action",
            "outcome",
            "trigger_context",
        )
        has_decision_key = any(k in data or k in ms or k in ep for k in decision_keys)

        if explicit_entry is None and not has_decision_key and not has_portfolio_decision:
            return None

        entry = explicit_entry or {}
        first_decision_item = None
        if isinstance(portfolio_source, list):
            for item in portfolio_source:
                if isinstance(item, dict) and (item.get("trade_state") or item.get("action") or item.get("scrutiny_audit")):
                    first_decision_item = item
                    break

        session_id = entry.get("session_id") or data.get("session_id") or uuid.uuid4().hex
        ticker = (
            entry.get("ticker")
            or (first_decision_item.get("ticker") if first_decision_item else None)
            or data.get("ticker")
            or "SESSION"
        )
        action_proposed = (
            entry.get("action_proposed")
            or entry.get("outcome")
            or data.get("action")
            or data.get("final_decision")
            or data.get("recommendation")
            or (first_decision_item.get("trade_state") or first_decision_item.get("action") if first_decision_item else None)
            or "HOLD"
        )
        decision_outcome = (
            entry.get("decision_outcome")
            or entry.get("outcome")
            or data.get("decision")
            or data.get("council_decision")
            or "AUTO_LOGGED"
        )
        raw_votes = entry.get("votes", entry.get("council_debate", data.get("council_debate", {})))
        if isinstance(raw_votes, dict):
            votes = raw_votes
        elif raw_votes is None:
            votes = {}
        else:
            votes = {"summary": raw_votes}

        transcript_text = entry.get(
            "transcript_text",
            entry.get("lesson", json.dumps(data, default=decimal_to_canonical_str)),
        )
        ts = entry.get("timestamp_utc") or data.get("timestamp_utc")
        if ts is None:
            cand_ts = entry.get("timestamp")
            if isinstance(cand_ts, str) and (cand_ts.endswith("Z") or cand_ts.endswith("+00:00")):
                ts = cand_ts
            else:
                ts = datetime.now(timezone.utc).isoformat()

        return {
            "session_id": session_id,
            "ticker": ticker,
            "action_proposed": action_proposed,
            "decision_outcome": decision_outcome,
            "votes": votes,
            "transcript_text": transcript_text,
            "timestamp_utc": ts,
        }

    def get_portfolio_basket(self) -> Dict[str, Any]:
        """Retrieves active holdings and cash balances from SQLite as exact Decimals."""
        try:
            with UnitOfWork(self._db_manager, auto_commit=False) as uow:
                cash_eur_rec = uow.cash.get_cash("PRIMARY", "EUR")
                cash_usd_rec = uow.cash.get_cash("PRIMARY", "USD")
                positions_recs = uow.positions.list_positions("PRIMARY")

                portfolio = []
                for p in positions_recs:
                    shares_val = validate_financial_decimal(
                        p.shares, f"shares for {p.ticker}", max_scale=4, allow_zero=True, non_negative=True
                    )
                    wac_val = validate_financial_decimal(
                        p.wac_eur, f"wac for {p.ticker}", max_scale=4, allow_zero=True, non_negative=True
                    )
                    if shares_val > Decimal("0"):
                        portfolio.append({
                            "ticker": p.ticker,
                            "shares": shares_val,
                            "wac": wac_val,
                        })

                eur_raw = cash_eur_rec.amount if cash_eur_rec else Decimal("0.00")
                usd_raw = cash_usd_rec.amount if cash_usd_rec else Decimal("0.00")
                eur_amount = validate_financial_decimal(
                    eur_raw, "unallocated_cash_eur", max_scale=2, allow_zero=True, non_negative=True
                )
                usd_amount = validate_financial_decimal(
                    usd_raw, "unallocated_cash_usd", max_scale=2, allow_zero=True, non_negative=True
                )
                quote = get_eurusd()

                return {
                    "portfolio": portfolio,
                    "unallocated_cash_eur": eur_amount,
                    "unallocated_cash_usd": usd_amount,
                    # Live rate, or None when no fresh quote exists (never a default rate)
                    "eurusd_rate": quote.rate if quote else None,
                    "eurusd_rate_as_of": quote.as_of_utc if quote else None,
                }
        except (PersistenceValidationError, PersistenceIntegrityError):
            raise
        except TransactionIntegrityError as tie:
            raise PersistenceIntegrityError(f"Transaction integrity failure: {tie}") from tie
        except Exception as e:
            raise PersistenceError(f"Failed to retrieve portfolio basket from SQLite: {e}") from e

    def save_portfolio_basket(
        self,
        portfolio: List[Dict[str, Any]],
        unallocated_cash_eur: Union[Decimal, int, str],
        unallocated_cash_usd: Optional[Union[Decimal, int, str]] = None,
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomically updates cash and position records inside a single UnitOfWork transaction.
        Enforces exact Decimal validation, authorization, and automatic rollback on failure.

        USD cash omitted (or 0 while EUR cash > 0) is derived from EUR at the live EUR/USD rate;
        with no fresh rate the stored USD cash is left unchanged rather than guessed.
        """
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource="portfolio_basket",
            payload={"portfolio": portfolio, "cash_eur": unallocated_cash_eur},
            caller_role=caller_role,
        )

        new_map = self._normalize_portfolio_items(portfolio)
        eur_dec = validate_financial_decimal(
            unallocated_cash_eur, "unallocated_cash_eur", max_scale=2, allow_zero=True, non_negative=True
        )
        usd_dec = (
            validate_financial_decimal(
                unallocated_cash_usd, "unallocated_cash_usd", max_scale=2, allow_zero=True, non_negative=True
            )
            if unallocated_cash_usd is not None
            else None
        )
        usd_dec = self._resolve_usd_cash(eur_dec, usd_dec)

        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                self._reconcile_portfolio_and_cash_in_uow(
                    uow=uow,
                    new_map=new_map,
                    eur_dec=eur_dec,
                    usd_dec=usd_dec,
                    caller_role=caller_role,
                )
                if usd_dec is None:
                    stored_usd = uow.cash.get_cash("PRIMARY", "USD")
                    usd_dec = stored_usd.amount if stored_usd else Decimal("0.00")

            return {
                "status": "success",
                "portfolio": [
                    {"ticker": t, "shares": d["shares"], "wac": d["wac"]}
                    for t, d in new_map.items()
                ],
                "unallocated_cash_eur": eur_dec,
                "unallocated_cash_usd": usd_dec,
            }
        except TransactionIntegrityError as tie:
            raise PersistenceIntegrityError(f"Transaction integrity failure: {tie}") from tie
        except (PermissionError, PersistenceValidationError):
            raise
        except ValidationError as ve:
            raise PersistenceValidationError(str(ve)) from ve
        except Exception as e:
            raise PersistenceError(f"Failed to save portfolio basket in SQLite: {e}") from e

    def update_ssot_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomically merges portfolio/cash updates, decision log entries, and extracted
        trade lessons inside a single SQLite UnitOfWork transaction.
        """
        data = self._parse_payload_input(payload)

        ms = data.get("mutable_state")
        if ms is not None and not isinstance(ms, dict):
            raise PersistenceValidationError("mutable_state in payload must be a dictionary")
        ms_dict = ms if isinstance(ms, dict) else {}

        ep = data.get("EXECUTION_PAYLOAD")
        if ep is not None and not isinstance(ep, dict):
            raise PersistenceValidationError("EXECUTION_PAYLOAD in payload must be a dictionary")
        ep_dict = ep if isinstance(ep, dict) else {}

        raw_portfolio = None
        has_portfolio = False
        for scope in (ms_dict, data, ep_dict):
            if "portfolio_snapshot" in scope:
                raw_portfolio = scope["portfolio_snapshot"]
                has_portfolio = True
                break
            if "portfolio" in scope:
                raw_portfolio = scope["portfolio"]
                has_portfolio = True
                break

        raw_eur = None
        has_eur = False
        for scope in (ms_dict, data, ep_dict):
            if "unallocated_cash_eur" in scope:
                raw_eur = scope["unallocated_cash_eur"]
                has_eur = True
                break

        raw_usd = None
        has_usd = False
        for scope in (ms_dict, data, ep_dict):
            if "unallocated_cash_usd" in scope:
                raw_usd = scope["unallocated_cash_usd"]
                has_usd = True
                break

        new_map = self._normalize_portfolio_items(raw_portfolio) if has_portfolio else None
        eur_dec = (
            validate_financial_decimal(
                raw_eur, "unallocated_cash_eur", max_scale=2, allow_zero=True, non_negative=True
            )
            if has_eur
            else None
        )
        usd_dec = (
            validate_financial_decimal(
                raw_usd, "unallocated_cash_usd", max_scale=2, allow_zero=True, non_negative=True
            )
            if has_usd
            else None
        )
        if eur_dec is not None and not has_usd:
            usd_dec = self._resolve_usd_cash(eur_dec, None)

        extracted_lessons = self._extract_lessons_from_payload(data)
        decision_params = self._extract_decision_params_from_payload(data, portfolio_source=raw_portfolio)

        # Pre-authorize all applicable mutations before opening the transaction
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource="ssot_payload",
            payload=data,
            caller_role=caller_role,
        )
        if decision_params is not None:
            authorize_financial_mutation(
                operation_name="update_decision_log",
                target_resource="decision_log",
                payload=decision_params,
                caller_role=caller_role,
            )
        if extracted_lessons:
            authorize_financial_mutation(
                operation_name="update_trade_lessons",
                target_resource="trade_lessons",
                payload=extracted_lessons,
                caller_role=caller_role,
            )

        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                if has_portfolio or has_eur or has_usd:
                    self._reconcile_portfolio_and_cash_in_uow(
                        uow=uow,
                        new_map=new_map,
                        eur_dec=eur_dec,
                        usd_dec=usd_dec,
                        caller_role=caller_role,
                    )

                if decision_params is not None:
                    uow.decision_log.append_decision(
                        session_id=decision_params["session_id"],
                        action_proposed=decision_params["action_proposed"],
                        decision_outcome=decision_params["decision_outcome"],
                        ticker=decision_params["ticker"],
                        votes=decision_params["votes"],
                        transcript_text=decision_params["transcript_text"],
                        caller_role=caller_role,
                        timestamp_utc=validate_timestamp_utc(decision_params["timestamp_utc"]),
                    )

                if extracted_lessons:
                    self._save_lessons_in_uow(
                        uow=uow,
                        raw_lessons=extracted_lessons,
                        caller_role=caller_role,
                    )

            return {"status": "success"}
        except TransactionIntegrityError as tie:
            raise PersistenceIntegrityError(f"Transaction integrity failure: {tie}") from tie
        except (PermissionError, PersistenceValidationError, PersistenceIntegrityError):
            raise
        except ValidationError as ve:
            raise PersistenceValidationError(f"Validation failed during composite SSoT update: {ve}") from ve
        except Exception as e:
            raise PersistenceError(f"Failed composite SSoT payload update in SQLite: {e}") from e

    def get_ssot_tickers(self) -> List[str]:
        """Returns all monitored tickers from active positions and watchlist."""
        combined: List[str] = []
        try:
            with UnitOfWork(self._db_manager, auto_commit=False) as uow:
                positions = uow.positions.list_positions("PRIMARY")
                for p in positions:
                    if p.shares > Decimal("0") and p.ticker not in combined:
                        combined.append(p.ticker)

                wl = uow.watchlist.list_watchlist()
                for item in wl:
                    if item.status == "ACTIVE" and item.ticker not in combined:
                        combined.append(item.ticker)
        except Exception:
            pass
        return combined

    def reconcile_ssot_format(self) -> None:
        """No-op for relational SQLite backend."""
        pass

    # -----------------------------------------------------------------------
    # Watchlist & Scout Operations
    # -----------------------------------------------------------------------

    def get_watchlist(self) -> List[str]:
        """Reads active watched tickers from watchlist table."""
        try:
            with UnitOfWork(self._db_manager, auto_commit=False) as uow:
                items = uow.watchlist.list_watchlist()
                return [i.ticker for i in items if i.status == "ACTIVE"]
        except Exception as e:
            raise PersistenceError(f"Failed to read watchlist from SQLite: {e}") from e

    def save_watchlist(
        self,
        watchlist: Union[List[str], Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> List[str]:
        """Atomically synchronizes watchlist records."""
        if isinstance(watchlist, dict):
            raw_list = watchlist.get("watchlist", [])
        elif isinstance(watchlist, list):
            raw_list = watchlist
        else:
            raw_list = []

        clean_list = [w.strip().upper() for w in raw_list if isinstance(w, str) and w.strip()]

        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource="watchlist",
            payload={"watchlist": clean_list},
            caller_role=caller_role,
        )

        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                existing = uow.watchlist.list_watchlist()
                existing_map = {i.ticker: i for i in existing}

                # Remove tickers no longer in watchlist
                for t in existing_map:
                    if t not in clean_list:
                        uow.watchlist.remove_watchlist_item(t, caller_role=caller_role)

                # Upsert current tickers
                for t in clean_list:
                    uow.watchlist.upsert_watchlist_item(
                        ticker=t,
                        status="ACTIVE",
                        notes="Stage 2 Synchronized",
                        caller_role=caller_role,
                    )
            return clean_list
        except PermissionError:
            raise
        except Exception as e:
            raise PersistenceError(f"Failed to save watchlist in SQLite: {e}") from e

    def get_scout_categories(self) -> Dict[str, Any]:
        """Reads scout category definitions from SQLite."""
        try:
            with UnitOfWork(self._db_manager, auto_commit=False) as uow:
                cats = uow.scout_categories.list_categories()
                return {c.category_name: c.tickers for c in cats}
        except Exception:
            return {}

    def save_scout_categories(
        self,
        categories: Union[Dict[str, Any], List[Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Replaces scout category definitions in SQLite atomically."""
        desired: Dict[str, Optional[List[str]]] = {}
        if isinstance(categories, dict):
            for name, tickers in categories.items():
                clean_name = str(name).strip()
                if not clean_name:
                    continue
                if isinstance(tickers, list):
                    desired[clean_name] = [str(t).strip() for t in tickers if str(t).strip()]
                elif tickers is None:
                    desired[clean_name] = []
                else:
                    desired[clean_name] = [str(tickers).strip()] if str(tickers).strip() else []
        elif isinstance(categories, list):
            for item in categories:
                if isinstance(item, str):
                    clean_name = item.strip()
                    if clean_name:
                        desired.setdefault(clean_name, None)
                elif isinstance(item, dict):
                    clean_name = str(item.get("category_name") or item.get("name") or "").strip()
                    if not clean_name:
                        continue
                    raw_tickers = item.get("tickers")
                    desired[clean_name] = (
                        [str(t).strip() for t in raw_tickers if str(t).strip()]
                        if isinstance(raw_tickers, list)
                        else None
                    )

        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                existing_cats = uow.scout_categories.list_categories()
                existing_by_name = {c.category_name: c for c in existing_cats}

                for cat in existing_cats:
                    if cat.category_name not in desired:
                        uow.scout_categories.delete_category(
                            category_id=cat.id,
                            caller_role=caller_role,
                        )

                for cat_name, tick_list in desired.items():
                    resolved_tickers = (
                        list(existing_by_name[cat_name].tickers)
                        if tick_list is None and cat_name in existing_by_name
                        else (tick_list or [])
                    )
                    uow.scout_categories.upsert_category(
                        category_name=cat_name,
                        description="Stage 2 Scout Category",
                        tickers=resolved_tickers,
                        caller_role=caller_role,
                    )
            return self.get_scout_categories()
        except PermissionError:
            raise
        except Exception as e:
            raise PersistenceError(f"Failed to save scout categories in SQLite: {e}") from e

    # -----------------------------------------------------------------------
    # Trade Lessons Operations
    # -----------------------------------------------------------------------

    def get_trade_lessons(self) -> List[Dict[str, Any]]:
        """Reads all trade lessons from trade_lessons table."""
        try:
            with UnitOfWork(self._db_manager, auto_commit=False) as uow:
                lessons = uow.trade_lessons.list_lessons()
                return [
                    {
                        "id": idx + 1,
                        "rule_code": l.rule_code,
                        "rule": f"{l.rule_code}: {l.directive_text}",
                        "directive_text": l.directive_text,
                        "category": l.category,
                        "status": l.status,
                    }
                    for idx, l in enumerate(lessons)
                ]
        except Exception as e:
            raise PersistenceError(f"Failed to read trade lessons from SQLite: {e}") from e

    def save_trade_lessons(
        self,
        lessons: Union[List[Dict[str, Any]], Dict[str, Any], str],
        caller_role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Upserts trade lessons into SQLite."""
        if isinstance(lessons, str):
            try:
                raw = json.loads(lessons)
            except Exception:
                raw = [{"rule": lessons}]
        elif isinstance(lessons, dict):
            raw = lessons.get("trade_lessons", [lessons])
        else:
            raw = lessons

        authorize_financial_mutation(
            operation_name="update_trade_lessons",
            target_resource="trade_lessons",
            payload=raw,
            caller_role=caller_role,
        )

        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                self._save_lessons_in_uow(uow=uow, raw_lessons=raw, caller_role=caller_role)
            return self.get_trade_lessons()
        except (PermissionError, PersistenceValidationError):
            raise
        except ValidationError as ve:
            raise PersistenceValidationError(f"Invalid trade lesson: {ve}") from ve
        except Exception as e:
            raise PersistenceError(f"Failed to save trade lessons in SQLite: {e}") from e

    # -----------------------------------------------------------------------
    # Decision Log Operations
    # -----------------------------------------------------------------------

    def get_decision_log(self) -> List[Dict[str, Any]]:
        """Reads decision records from decision_log table."""
        try:
            with UnitOfWork(self._db_manager, auto_commit=False) as uow:
                recs = uow.decision_log.list_decisions(limit=300)
                return [
                    {
                        "session_id": r.session_id,
                        "ticker": r.ticker,
                        "action_proposed": r.action_proposed,
                        "decision_outcome": r.decision_outcome,
                        "votes": r.votes,
                        "transcript_text": r.transcript_text,
                        "timestamp": r.timestamp_utc,
                    }
                    for r in recs
                ]
        except Exception as e:
            raise PersistenceError(f"Failed to read decision log from SQLite: {e}") from e

    def append_decision_log(
        self,
        entry: Dict[str, Any],
        prepend: bool = False,
        caller_role: Optional[str] = None,
    ) -> None:
        """Appends a decision log record into SQLite."""
        authorize_financial_mutation(
            operation_name="update_decision_log",
            target_resource="decision_log",
            payload=entry,
            caller_role=caller_role,
        )

        session_id = entry.get("session_id") or uuid.uuid4().hex
        ticker = entry.get("ticker", "SESSION")
        action = entry.get("action_proposed", entry.get("outcome", "HOLD"))
        outcome = entry.get("decision_outcome", entry.get("outcome", "AUTO_LOGGED"))
        votes = entry.get("votes", entry.get("council_debate", {}))
        transcript = entry.get("transcript_text", entry.get("lesson", json.dumps(entry)))
        ts = entry.get("timestamp", entry.get("timestamp_utc", datetime.now(timezone.utc).isoformat()))

        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                uow.decision_log.append_decision(
                    session_id=session_id,
                    action_proposed=action,
                    decision_outcome=outcome,
                    ticker=ticker,
                    votes=votes,
                    transcript_text=transcript,
                    caller_role=caller_role,
                    timestamp_utc=validate_timestamp_utc(ts),
                )
        except PermissionError:
            raise
        except Exception as e:
            raise PersistenceError(f"Failed to append decision log to SQLite: {e}") from e

    def append_decision_log_from_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> bool:
        """Extracts turn decisions from incoming council payload and logs them into SQLite."""
        entry = {
            "session_id": uuid.uuid4().hex,
            "ticker": "SESSION",
            "action_proposed": "HOLD",
            "decision_outcome": "AUTO_LOGGED",
            "transcript_text": (
                json.dumps(payload, default=decimal_to_canonical_str)
                if isinstance(payload, dict)
                else str(payload)
            ),
        }
        self.append_decision_log(entry, caller_role=caller_role)
        return True

    def clear_decision_log(self, caller_role: Optional[str] = None) -> None:
        """Clears all decision log entries."""
        authorize_financial_mutation(
            operation_name="update_decision_log",
            target_resource="decision_log",
            payload={"action": "clear"},
            caller_role=caller_role,
        )

        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                uow.connection.execute("DELETE FROM decision_log")
        except PermissionError:
            raise
        except Exception as e:
            raise PersistenceError(f"Failed to clear decision log in SQLite: {e}") from e

    def close(self) -> None:
        """Disposes resources and connections cleanly."""
        if hasattr(self._db_manager, "close"):
            self._db_manager.close()
