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
from decimal import Decimal
import json
import re
from typing import Any, Dict, List, Optional, Union
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
    from tool_registry import authorize_financial_mutation, get_active_caller_role

from .contracts import (
    PersistenceAdapter,
    PersistenceValidationError,
    PersistenceIntegrityError,
    PersistenceError,
    validate_finite_float,
)


class SqlitePersistenceAdapter(PersistenceAdapter):
    """
    SQLite persistence adapter utilizing existing UnitOfWork and repository infrastructure.
    Used in controlled integration and automated testing environments.
    """

    def __init__(self, db_manager: DatabaseManager):
        if db_manager is None:
            raise ValueError("db_manager cannot be None")
        self._db_manager = db_manager

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
        """Projects current SQLite state into a canonical SSoT dictionary."""
        basket = self.get_portfolio_basket()
        watchlist = self.get_watchlist()
        scouts = self.get_scout_categories()

        return {
            "portfolio_snapshot": basket["portfolio"],
            "unallocated_cash_eur": basket["unallocated_cash_eur"],
            "unallocated_cash_usd": basket["unallocated_cash_usd"],
            "watched_tickers": watchlist,
            "scout_categories": scouts,
            "mutable_state": {
                "portfolio_snapshot": basket["portfolio"],
                "unallocated_cash_eur": basket["unallocated_cash_eur"],
                "unallocated_cash_usd": basket["unallocated_cash_usd"],
                "watched_tickers": watchlist,
                "scout_categories": scouts,
            },
        }

    def get_portfolio_basket(self) -> Dict[str, Any]:
        """Retrieves active holdings and cash balances from SQLite."""
        try:
            with UnitOfWork(self._db_manager, auto_commit=False) as uow:
                cash_eur_rec = uow.cash.get_cash("PRIMARY", "EUR")
                cash_usd_rec = uow.cash.get_cash("PRIMARY", "USD")
                positions_recs = uow.positions.list_positions("PRIMARY")

                portfolio = []
                for p in positions_recs:
                    shares_val = validate_finite_float(p.shares, f"shares for {p.ticker}")
                    wac_val = validate_finite_float(p.wac_eur, f"wac for {p.ticker}")
                    if shares_val > 0.0:
                        portfolio.append({
                            "ticker": p.ticker,
                            "shares": shares_val,
                            "wac": wac_val,
                        })

                eur_amount = validate_finite_float(cash_eur_rec.amount if cash_eur_rec else 0.0, "unallocated_cash_eur")
                usd_amount = validate_finite_float(cash_usd_rec.amount if cash_usd_rec else 0.0, "unallocated_cash_usd")

                return {
                    "portfolio": portfolio,
                    "unallocated_cash_eur": eur_amount,
                    "unallocated_cash_usd": usd_amount,
                    "eurusd_rate": 1.08,
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
        unallocated_cash_eur: float,
        unallocated_cash_usd: float = 0.0,
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomically updates cash and position records inside a single UnitOfWork transaction.
        Enforces Decimal validation, authorization, and automatic rollback on failure.
        """
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource="portfolio_basket",
            payload={"portfolio": portfolio, "cash_eur": unallocated_cash_eur},
            caller_role=caller_role,
        )

        if not isinstance(portfolio, list):
            raise PersistenceValidationError("Portfolio must be a list of position dictionaries")

        new_map: Dict[str, Dict[str, Any]] = {}
        for item in portfolio:
            if not isinstance(item, dict):
                raise PersistenceValidationError("Portfolio item must be a dictionary")
            t = str(item.get("ticker", "")).strip().upper()
            if not t:
                raise PersistenceValidationError("Portfolio item missing ticker")
            shares = validate_finite_float(item.get("shares", 0.0), f"shares for {t}")
            wac = validate_finite_float(item.get("wac", 0.0), f"wac for {t}")
            if shares > 0.0:
                new_map[t] = {
                    "shares": Decimal(str(round(shares, 4))),
                    "wac": Decimal(str(round(wac, 4))),
                }

        eur_cash = validate_finite_float(unallocated_cash_eur, "unallocated_cash_eur")
        usd_cash = validate_finite_float(unallocated_cash_usd, "unallocated_cash_usd")
        eur_dec = Decimal(str(round(eur_cash, 2)))
        usd_val = usd_cash if usd_cash > 0.0 else round(eur_cash * 1.08, 2)
        usd_dec = Decimal(str(round(usd_val, 2)))

        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                # 1. Update cash balances
                uow.cash.set_cash_balance(
                    account_id="PRIMARY",
                    currency="EUR",
                    amount=eur_dec,
                    caller_role=caller_role,
                )

                uow.cash.set_cash_balance(
                    account_id="PRIMARY",
                    currency="USD",
                    amount=usd_dec,
                    caller_role=caller_role,
                )

                # 2. Reconcile positions atomically
                existing_positions = uow.positions.list_positions("PRIMARY")
                existing_map = {p.ticker: p for p in existing_positions}

                # Delete positions not present in new basket
                for t in existing_map:
                    if t not in new_map:
                        uow.positions.delete_position("PRIMARY", t, caller_role=caller_role)

                # Upsert positions
                for t, data in new_map.items():
                    uow.positions.upsert_position(
                        account_id="PRIMARY",
                        ticker=t,
                        shares=data["shares"],
                        wac_eur=data["wac"],
                        caller_role=caller_role,
                    )

            return {
                "status": "success",
                "portfolio": [
                    {"ticker": t, "shares": float(d["shares"]), "wac": float(d["wac"])}
                    for t, d in new_map.items()
                ],
                "unallocated_cash_eur": float(eur_dec),
                "unallocated_cash_usd": float(usd_dec),
            }
        except TransactionIntegrityError as tie:
            raise PersistenceIntegrityError(f"Transaction integrity failure: {tie}") from tie
        except (PermissionError, PersistenceValidationError):
            raise
        except Exception as e:
            raise PersistenceError(f"Failed to save portfolio basket in SQLite: {e}") from e

    def update_ssot_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Merges an execution payload into SQLite persistence via UnitOfWork."""
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource="ssot_payload",
            payload=payload,
            caller_role=caller_role,
        )

        if isinstance(payload, str):
            clean_str = payload.strip()
            if "```" in clean_str:
                m = re.search(r"```(?:json)?\s*(.*?)\s*```", clean_str, re.IGNORECASE | re.DOTALL)
                if m:
                    clean_str = m.group(1).strip()
            data = json.loads(clean_str)
        else:
            data = payload

        # Extract portfolio and cash
        ms = data.get("mutable_state", {}) if isinstance(data.get("mutable_state"), dict) else data
        raw_portfolio = ms.get("portfolio_snapshot") or data.get("portfolio_snapshot") or []
        eur_cash = ms.get("unallocated_cash_eur") or data.get("unallocated_cash_eur") or 0.0
        usd_cash = ms.get("unallocated_cash_usd") or data.get("unallocated_cash_usd") or 0.0

        if raw_portfolio or eur_cash > 0:
            self.save_portfolio_basket(raw_portfolio, float(eur_cash), float(usd_cash), caller_role=caller_role)

        # Extract lessons
        extracted_lessons = []
        for key in ("trade_lessons", "new_trade_lessons", "compressed_trade_lessons"):
            val = data.get(key) or ms.get(key)
            if isinstance(val, list):
                extracted_lessons.extend(val)
        if extracted_lessons:
            self.save_trade_lessons(extracted_lessons, caller_role=caller_role)

        return {"status": "success"}

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
        """Upserts scout category records into SQLite."""
        try:
            with UnitOfWork(self._db_manager, auto_commit=True) as uow:
                if isinstance(categories, dict):
                    for name, tickers in categories.items():
                        tick_list = tickers if isinstance(tickers, list) else [str(tickers)]
                        uow.scout_categories.upsert_category(
                            name=str(name),
                            description="Stage 2 Scout Category",
                            tickers=tick_list,
                        )
                elif isinstance(categories, list):
                    for item in categories:
                        name = item if isinstance(item, str) else item.get("category_name", "UNKNOWN")
                        uow.scout_categories.upsert_category(
                            name=str(name),
                            description="Stage 2 Scout Category",
                            tickers=[],
                        )
            return self.get_scout_categories()
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
                for idx, item in enumerate(raw):
                    if isinstance(item, str):
                        code = f"L-{idx + 1}"
                        directive = item
                    elif isinstance(item, dict):
                        rule_text = item.get("rule", item.get("directive_text", ""))
                        if ":" in rule_text:
                            parts = rule_text.split(":", 1)
                            code = parts[0].strip()
                            directive = parts[1].strip()
                        else:
                            code = item.get("rule_code", f"L-{idx + 1}")
                            directive = rule_text or "Default Directive"
                    else:
                        continue

                    # Clean code to uppercase alphanumeric identifier
                    clean_code = re.sub(r"[^A-Za-z0-9_-]", "", code).upper() or f"L-{idx + 1}"
                    uow.trade_lessons.upsert_lesson(
                        rule_code=clean_code,
                        directive_text=directive,
                        category="RISK",
                        status="ACTIVE",
                    )
            return self.get_trade_lessons()
        except PermissionError:
            raise
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
            "transcript_text": json.dumps(payload) if isinstance(payload, dict) else str(payload),
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
