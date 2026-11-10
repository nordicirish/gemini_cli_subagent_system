"""
Offline Legacy JSON Importer.
Governed by Stage 1.5-D Architecture and Agent Governance Framework.
"""

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from python.database.manager import (
    DatabaseManager,
    PRIMARY_FINANCIAL_PROFILE,
)
from python.database.schema import SchemaMigrator
from python.database.repositories.base import (
    decimal_to_canonical_str,
    get_db_revision,
    utc_now_iso,
    ValidationError,
)
from python.database.repositories.unit_of_work import UnitOfWork
from python.database.migration.models import (
    MigrationError,
    MigrationSafetyError,
    MigrationValidationError,
    MigrationConflictError,
    MigrationSource,
    MigrationPlan,
    MigrationResult,
)
from python.database.migration.contract import (
    MIGRATION_CONTRACT_VERSION,
    SQLITE_OWNED_DOMAINS,
    EXTERNALLY_MANAGED_SECTIONS,
    CASH_EQUALITY_FIELDS,
    POSITION_EQUALITY_FIELDS,
    WATCHLIST_EQUALITY_FIELDS,
    SCOUT_CATEGORY_EQUALITY_FIELDS,
    TRADE_LESSON_EQUALITY_FIELDS,
    DECISION_LOG_EQUALITY_FIELDS,
    records_equal,
    _reject_json_constant,
    _validate_json_dict_keys,
    load_strict_json,
    load_strict_json_file,
    parse_financial_decimal,
    parse_ticker,
    parse_iso_timestamp,
    resolve_historical_timestamp,
    transform_legacy_decision_case_b,
    transform_legacy_decision_case_c,
    validate_migration_destination,
    extract_rule_code_and_directive,
)
from python.tool_registry import execution_role_context

__all__ = ["LegacyJsonImporter"]


class LegacyJsonImporter:
    """
    Offline importer parsing and ingesting legacy JSON documents into disposable SQLite databases.
    Guarantees:
    - Disposable-only target database safety (strictly rejects production context/gem.db).
    - Decimal financial integrity without float conversions.
    - Transactional atomicity: complete rollback upon any error.
    - Safe repeatability: identical records skipped; conflicting records rejected.
    """

    def __init__(self, repository_root: Optional[Union[Path, str]] = None):
        if repository_root is None:
            self._repo_root = Path(__file__).resolve().parent.parent.parent.parent
        else:
            self._repo_root = Path(repository_root).resolve()

    @property
    def repository_root(self) -> Path:
        return self._repo_root

    def load_source_file(self, file_path: Union[Path, str]) -> MigrationSource:
        """Reads a JSON file from disk with unified strict parsing and Decimal numbers (D-05)."""
        data, sha256, p = load_strict_json_file(file_path, repository_root=self._repo_root)
        return MigrationSource(
            name=p.name,
            data=data,
            source_path=p,
            file_sha256=sha256,
        )

    def validate_sources(
        self,
        sources: Union[Dict[str, Any], Sequence[Union[Path, str, MigrationSource]], Path, str, MigrationSource],
    ) -> List[MigrationSource]:
        """
        Validates and normalizes legacy sources into structured MigrationSource instances.
        Rejects invalid types, non-string keys, and malformed JSON structures.
        """
        loaded_sources: List[MigrationSource] = []

        if isinstance(sources, MigrationSource):
            _validate_json_dict_keys(sources.data, path=sources.name)
            return [sources]

        if isinstance(sources, (Path, str)):
            return [self.load_source_file(sources)]

        if isinstance(sources, dict):
            # Check if this is a dictionary of sources (mapping file path / source name -> data/path/source)
            # or a single source data dictionary (e.g. {"mutable_state": ...} or {"portfolio_cash": [...]})
            is_file_map = (
                bool(sources)
                and all(
                    (isinstance(k, str) and (k.endswith(".json") or "/" in k or "\\" in k))
                    for k in sources.keys()
                )
                and all(isinstance(v, (dict, Path, str, MigrationSource)) for v in sources.values())
            )
            if is_file_map:
                for name, content in sources.items():
                    if isinstance(content, dict):
                        _validate_json_dict_keys(content, path=name)
                        loaded_sources.append(MigrationSource(name=name, data=content))
                    elif isinstance(content, (str, Path)):
                        loaded_sources.append(self.load_source_file(content))
                    elif isinstance(content, MigrationSource):
                        _validate_json_dict_keys(content.data, path=content.name)
                        loaded_sources.append(content)
                    else:
                        raise MigrationValidationError(f"Unsupported source entry value for {name}: {type(content)}")
            else:
                _validate_json_dict_keys(sources, path="source_dict")
                loaded_sources.append(MigrationSource(name="source_dict", data=sources))

        elif isinstance(sources, (list, tuple)):
            for item in sources:
                if isinstance(item, MigrationSource):
                    _validate_json_dict_keys(item.data, path=item.name)
                    loaded_sources.append(item)
                elif isinstance(item, (str, Path)):
                    loaded_sources.append(self.load_source_file(item))
                elif isinstance(item, dict):
                    _validate_json_dict_keys(item, path="inline_source")
                    loaded_sources.append(MigrationSource(name="inline_source", data=item))
                else:
                    raise MigrationValidationError(f"Unsupported source item type: {type(item)}")
        else:
            raise MigrationValidationError(f"Sources must be a dict, sequence, path, or MigrationSource, got {type(sources)}")

        return loaded_sources

    def plan_migration(
        self,
        sources: Union[Dict[str, Any], Sequence[Union[Path, str, MigrationSource]]],
        default_timestamp: Optional[str] = None,
        default_account_id: str = "PRIMARY",
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> MigrationPlan:
        """
        Constructs an in-memory migration plan without modifying any database.
        Extracts entities across supported SQLite domains, detects schema gaps,
        and isolates externally managed sections.
        """
        validated_sources = self.validate_sources(sources)
        plan = MigrationPlan()

        if default_timestamp is not None:
            default_timestamp = parse_iso_timestamp(default_timestamp, "default_timestamp")

        seen_cash_records: Dict[Tuple[str, str], Dict[str, Any]] = {}
        seen_position_records: Dict[Tuple[str, str], Dict[str, Any]] = {}
        seen_watchlist_records: Dict[str, Dict[str, Any]] = {}
        seen_category_records: Dict[str, Dict[str, Any]] = {}
        seen_lesson_records: Dict[str, Dict[str, Any]] = {}
        seen_decision_records: Dict[Tuple[str, str, Optional[str]], Dict[str, Any]] = {}

        for src in validated_sources:
            data = src.data

            # ---------------------------------------------------------------
            # 1. Non-Tabular & Externally Managed Sections Isolation
            # ---------------------------------------------------------------
            for ext_key in EXTERNALLY_MANAGED_SECTIONS:
                if ext_key in data:
                    plan.external_sections[ext_key] = data[ext_key]

            # ---------------------------------------------------------------
            # 2. Portfolio Cash Domain
            # ---------------------------------------------------------------
            # Check structured portfolio_cash list
            raw_cash_list = data.get("portfolio_cash")
            if isinstance(raw_cash_list, list):
                for idx, c in enumerate(raw_cash_list):
                    if not isinstance(c, dict):
                        raise MigrationValidationError(f"{src.name}: portfolio_cash[{idx}] must be a dict")
                    account = str(c.get("account_id") or default_account_id).strip()
                    currency = str(c.get("currency") or "EUR").strip().upper()
                    if currency not in ("EUR", "USD"):
                        raise MigrationValidationError(f"{src.name}: unsupported currency '{currency}'")
                    amount = parse_financial_decimal(c.get("amount"), f"portfolio_cash[{idx}].amount", max_scale=2)
                    ts_valid, _ = resolve_historical_timestamp(
                        c.get("last_updated_at_utc"),
                        f"{src.name}: portfolio_cash[{idx}].last_updated_at_utc",
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                    key = (account, currency)
                    cash_rec = {
                        "account_id": account,
                        "currency": currency,
                        "amount": amount,
                        "timestamp_utc": ts_valid,
                    }
                    if key in seen_cash_records:
                        prev = seen_cash_records[key]
                        if records_equal(prev, cash_rec, CASH_EQUALITY_FIELDS):
                            plan.discrepancies.append({
                                "domain": "portfolio_cash",
                                "key": key,
                                "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                "message": f"Exact duplicate cash record for {account}/{currency} deduplicated.",
                            })
                        else:
                            raise MigrationConflictError(
                                f"Conflicting duplicate cash record for {account}/{currency} in {src.name}: "
                                f"existing {prev} != incoming {cash_rec}"
                            )
                    else:
                        seen_cash_records[key] = cash_rec
                        plan.cash_records.append(cash_rec)

            # Check scalar cash fields (e.g. unallocated_cash_eur / unallocated_cash_usd)
            for curr, field_key in [("EUR", "unallocated_cash_eur"), ("USD", "unallocated_cash_usd")]:
                val = data.get(field_key)
                if val is None and "mutable_state" in data and isinstance(data["mutable_state"], dict):
                    val = data["mutable_state"].get(field_key)
                if val is not None:
                    amount = parse_financial_decimal(val, f"{src.name}.{field_key}", max_scale=2)
                    ts_valid, _ = resolve_historical_timestamp(
                        None,
                        f"{src.name}.{field_key}",
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                    key = (default_account_id, curr)
                    cash_rec = {
                        "account_id": default_account_id,
                        "currency": curr,
                        "amount": amount,
                        "timestamp_utc": ts_valid,
                    }
                    if key in seen_cash_records:
                        prev = seen_cash_records[key]
                        if records_equal(prev, cash_rec, CASH_EQUALITY_FIELDS):
                            plan.discrepancies.append({
                                "domain": "portfolio_cash",
                                "key": key,
                                "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                "message": f"Exact duplicate scalar cash record for {curr} deduplicated.",
                            })
                        else:
                            raise MigrationConflictError(
                                f"Conflicting duplicate cash record for {default_account_id}/{curr} in {src.name}.{field_key}: "
                                f"existing {prev} != incoming {cash_rec}"
                            )
                    else:
                        seen_cash_records[key] = cash_rec
                        plan.cash_records.append(cash_rec)

            # ---------------------------------------------------------------
            # 3. Portfolio Positions Domain
            # ---------------------------------------------------------------
            raw_pos_list = data.get("portfolio_snapshot") or data.get("portfolio_positions")
            if raw_pos_list is None and "mutable_state" in data and isinstance(data["mutable_state"], dict):
                raw_pos_list = data["mutable_state"].get("portfolio_snapshot") or data["mutable_state"].get("portfolio_positions")

            if isinstance(raw_pos_list, list):
                for idx, p in enumerate(raw_pos_list):
                    if not isinstance(p, dict):
                        raise MigrationValidationError(f"{src.name}: portfolio_snapshot[{idx}] must be a dict")
                    account = str(p.get("account_id") or default_account_id).strip()
                    ticker = parse_ticker(p.get("ticker"), f"portfolio_snapshot[{idx}].ticker")
                    shares = parse_financial_decimal(p.get("shares"), f"portfolio_snapshot[{idx}].shares", max_scale=4)

                    wac_eur_raw = p.get("wac_eur") or p.get("wac")
                    if wac_eur_raw is None:
                        raise MigrationValidationError(f"{src.name}: portfolio_snapshot[{idx}] missing required wac/wac_eur")
                    wac_eur = parse_financial_decimal(wac_eur_raw, f"portfolio_snapshot[{idx}].wac_eur", max_scale=4)

                    wac_usd_raw = p.get("wac_usd")
                    wac_usd = (
                        parse_financial_decimal(wac_usd_raw, f"portfolio_snapshot[{idx}].wac_usd", max_scale=4)
                        if wac_usd_raw is not None
                        else None
                    )

                    ts_valid, _ = resolve_historical_timestamp(
                        p.get("last_updated_at_utc"),
                        f"{src.name}: portfolio_snapshot[{idx}].last_updated_at_utc",
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )

                    key = (account, ticker)
                    pos_rec = {
                        "account_id": account,
                        "ticker": ticker,
                        "shares": shares,
                        "wac_eur": wac_eur,
                        "wac_usd": wac_usd,
                        "timestamp_utc": ts_valid,
                    }
                    if key in seen_position_records:
                        prev = seen_position_records[key]
                        if records_equal(prev, pos_rec, POSITION_EQUALITY_FIELDS):
                            plan.discrepancies.append({
                                "domain": "portfolio_positions",
                                "key": key,
                                "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                "message": f"Exact duplicate position record for {account}/{ticker} deduplicated.",
                            })
                        else:
                            raise MigrationConflictError(
                                f"Conflicting duplicate position record for {account}/{ticker} in {src.name}: "
                                f"existing {prev} != incoming {pos_rec}"
                            )
                    else:
                        seen_position_records[key] = pos_rec
                        plan.position_records.append(pos_rec)

            # ---------------------------------------------------------------
            # 4. Watchlist Domain
            # ---------------------------------------------------------------
            # Check watched_tickers or WATCHLIST or structured watchlist
            raw_wl = data.get("watchlist")
            if isinstance(raw_wl, list) and raw_wl and isinstance(raw_wl[0], dict):
                for idx, item in enumerate(raw_wl):
                    ticker = parse_ticker(item.get("ticker"), f"watchlist[{idx}].ticker")
                    status = str(item.get("status") or "ACTIVE").strip().upper()
                    notes = item.get("notes")
                    ts_valid, _ = resolve_historical_timestamp(
                        item.get("added_at_utc"),
                        f"{src.name}: watchlist[{idx}].added_at_utc",
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                    wl_rec = {
                        "ticker": ticker,
                        "status": status,
                        "notes": notes,
                        "timestamp_utc": ts_valid,
                    }
                    if ticker in seen_watchlist_records:
                        prev = seen_watchlist_records[ticker]
                        if records_equal(prev, wl_rec, WATCHLIST_EQUALITY_FIELDS):
                            plan.discrepancies.append({
                                "domain": "watchlist",
                                "ticker": ticker,
                                "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                "message": f"Exact duplicate watchlist record for {ticker} deduplicated.",
                            })
                        else:
                            raise MigrationConflictError(
                                f"Conflicting duplicate watchlist record for {ticker} in {src.name}: "
                                f"existing {prev} != incoming {wl_rec}"
                            )
                    else:
                        seen_watchlist_records[ticker] = wl_rec
                        plan.watchlist_records.append(wl_rec)
            else:
                raw_tickers = (
                    data.get("watched_tickers")
                    or (data.get("mutable_state", {}).get("watched_tickers") if isinstance(data.get("mutable_state"), dict) else None)
                    or data.get("WATCHLIST")
                )
                if isinstance(raw_tickers, list):
                    for item in raw_tickers:
                        ticker = parse_ticker(item, f"{src.name}.watched_tickers")
                        ts_valid, _ = resolve_historical_timestamp(
                            None,
                            f"{src.name}.watched_tickers[{ticker}]",
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                        wl_rec = {
                            "ticker": ticker,
                            "status": "ACTIVE",
                            "notes": None,
                            "timestamp_utc": ts_valid,
                        }
                        if ticker in seen_watchlist_records:
                            prev = seen_watchlist_records[ticker]
                            if records_equal(prev, wl_rec, WATCHLIST_EQUALITY_FIELDS):
                                plan.discrepancies.append({
                                    "domain": "watchlist",
                                    "ticker": ticker,
                                    "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                    "message": f"Exact duplicate watched ticker {ticker} deduplicated.",
                                })
                            else:
                                raise MigrationConflictError(
                                    f"Conflicting duplicate watchlist record for {ticker} in {src.name}: "
                                    f"existing {prev} != incoming {wl_rec}"
                                )
                        else:
                            seen_watchlist_records[ticker] = wl_rec
                            plan.watchlist_records.append(wl_rec)

            # ---------------------------------------------------------------
            # 5. Scout Categories Domain
            # ---------------------------------------------------------------
            # Check SCOUT_TICKER_MAP in config.json
            scout_map = data.get("SCOUT_TICKER_MAP")
            if isinstance(scout_map, dict):
                for cat_name, tickers in scout_map.items():
                    norm_cat = str(cat_name).strip()
                    if not norm_cat:
                        continue
                    parsed_tickers = [parse_ticker(t, f"scout[{norm_cat}].ticker") for t in (tickers or [])]
                    ts_valid, _ = resolve_historical_timestamp(
                        None,
                        f"{src.name}.SCOUT_TICKER_MAP[{norm_cat}]",
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                    cat_rec = {
                        "category_name": norm_cat,
                        "description": None,
                        "tickers": sorted(parsed_tickers),
                        "timestamp_utc": ts_valid,
                    }
                    if norm_cat in seen_category_records:
                        prev = seen_category_records[norm_cat]
                        if records_equal(prev, cat_rec, SCOUT_CATEGORY_EQUALITY_FIELDS):
                            plan.discrepancies.append({
                                "domain": "scout_categories",
                                "category_name": norm_cat,
                                "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                "message": f"Exact duplicate scout category record for '{norm_cat}' deduplicated.",
                            })
                        else:
                            raise MigrationConflictError(
                                f"Conflicting duplicate scout category record for '{norm_cat}' in {src.name}: "
                                f"existing {prev} != incoming {cat_rec}"
                            )
                    else:
                        seen_category_records[norm_cat] = cat_rec
                        plan.scout_categories.append(cat_rec)

            # Check structured scout_categories
            scout_cats = data.get("scout_categories")
            if isinstance(scout_cats, list) and scout_cats and isinstance(scout_cats[0], dict):
                for idx, sc in enumerate(scout_cats):
                    norm_cat = str(sc.get("category_name") or "").strip()
                    if not norm_cat:
                        continue
                    desc = sc.get("description")
                    parsed_tickers = [parse_ticker(t, f"scout[{norm_cat}].ticker") for t in (sc.get("tickers") or [])]
                    ts_valid, _ = resolve_historical_timestamp(
                        sc.get("updated_at_utc"),
                        f"{src.name}: scout[{norm_cat}].updated_at_utc",
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                    cat_rec = {
                        "category_name": norm_cat,
                        "description": desc,
                        "tickers": sorted(parsed_tickers),
                        "timestamp_utc": ts_valid,
                    }
                    if norm_cat in seen_category_records:
                        prev = seen_category_records[norm_cat]
                        if records_equal(prev, cat_rec, SCOUT_CATEGORY_EQUALITY_FIELDS):
                            plan.discrepancies.append({
                                "domain": "scout_categories",
                                "category_name": norm_cat,
                                "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                "message": f"Exact duplicate scout category record for '{norm_cat}' deduplicated.",
                            })
                        else:
                            raise MigrationConflictError(
                                f"Conflicting duplicate scout category record for '{norm_cat}' in {src.name}: "
                                f"existing {prev} != incoming {cat_rec}"
                            )
                    else:
                        seen_category_records[norm_cat] = cat_rec
                        plan.scout_categories.append(cat_rec)

            # ---------------------------------------------------------------
            # 6. Trade Lessons Domain
            # ---------------------------------------------------------------
            raw_lessons = data.get("trade_lessons")
            if isinstance(raw_lessons, list):
                for idx, item in enumerate(raw_lessons):
                    if not isinstance(item, dict):
                        raise MigrationValidationError(f"{src.name}: trade_lessons[{idx}] must be a dict")

                    # Case A: Canonical structured record
                    if "rule_code" in item and "directive_text" in item:
                        code = str(item["rule_code"]).strip().upper()
                        directive = str(item["directive_text"]).strip()
                        category = str(item.get("category") or "MANDATE").strip().upper()
                        status = str(item.get("status") or "ACTIVE").strip().upper()
                        ts_created, _ = resolve_historical_timestamp(
                            item.get("created_at_utc"),
                            f"{src.name}: trade_lessons[{idx}].created_at_utc",
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                        ts_reviewed, _ = resolve_historical_timestamp(
                            item.get("last_reviewed_at_utc"),
                            f"{src.name}: trade_lessons[{idx}].last_reviewed_at_utc",
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                        lesson_rec = {
                            "rule_code": code,
                            "directive_text": directive,
                            "category": category,
                            "status": status,
                            "created_at_utc": ts_created,
                            "last_reviewed_at_utc": ts_reviewed,
                        }
                        if code in seen_lesson_records:
                            prev = seen_lesson_records[code]
                            if records_equal(prev, lesson_rec, TRADE_LESSON_EQUALITY_FIELDS):
                                plan.discrepancies.append({
                                    "domain": "trade_lessons",
                                    "rule_code": code,
                                    "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                    "message": f"Exact duplicate trade lesson record for '{code}' deduplicated.",
                                })
                            else:
                                raise MigrationConflictError(
                                    f"Conflicting duplicate trade lesson record for '{code}' in {src.name}: "
                                    f"existing {prev} != incoming {lesson_rec}"
                                )
                        else:
                            seen_lesson_records[code] = lesson_rec
                            plan.trade_lessons.append(lesson_rec)

                    # Case B: Legacy format {"id": N, "rule": "L-xxx: ..."}
                    elif "rule" in item:
                        raw_rule = str(item["rule"])
                        extracted_code, directive = extract_rule_code_and_directive(raw_rule)
                        if extracted_code is None:
                            plan.discrepancies.append({
                                "domain": "trade_lessons",
                                "item_id": item.get("id"),
                                "reason": "UNCODIFIED_RULE_NO_L_PREFIX",
                                "message": f"Legacy rule item {item.get('id')} lacks an explicit 'L-xxx' prefix; skipped fail-closed.",
                                "raw_rule": raw_rule[:60],
                            })
                        else:
                            if default_timestamp is None:
                                plan.discrepancies.append({
                                    "domain": "trade_lessons",
                                    "rule_code": extracted_code,
                                    "reason": "MISSING_TIMESTAMPS",
                                    "message": f"Legacy trade lesson {extracted_code} missing timestamps and no default provided",
                                })
                            else:
                                lesson_rec = {
                                    "rule_code": extracted_code,
                                    "directive_text": directive or raw_rule,
                                    "category": "MANDATE",
                                    "status": "ACTIVE",
                                    "created_at_utc": default_timestamp,
                                    "last_reviewed_at_utc": default_timestamp,
                                }
                                if extracted_code in seen_lesson_records:
                                    prev = seen_lesson_records[extracted_code]
                                    if records_equal(prev, lesson_rec, TRADE_LESSON_EQUALITY_FIELDS):
                                        plan.discrepancies.append({
                                            "domain": "trade_lessons",
                                            "rule_code": extracted_code,
                                            "type": "EXACT_DUPLICATE_DEDUPLICATED",
                                            "message": f"Exact duplicate trade lesson record for '{extracted_code}' deduplicated.",
                                        })
                                    else:
                                        raise MigrationConflictError(
                                            f"Conflicting duplicate trade lesson record for '{extracted_code}' in {src.name}: "
                                            f"existing {prev} != incoming {lesson_rec}"
                                        )
                                else:
                                    seen_lesson_records[extracted_code] = lesson_rec
                                    plan.trade_lessons.append(lesson_rec)

            # ---------------------------------------------------------------
            # 7. Decision Log Domain
            # ---------------------------------------------------------------
            def _add_decision_record(
                session_id: str,
                action_proposed: str,
                decision_outcome: str,
                ticker: Optional[str],
                votes: Dict[str, Any],
                transcript_text: Optional[str],
                timestamp_utc: str,
                src_name: str,
            ) -> None:
                dec_rec = {
                    "session_id": session_id,
                    "action_proposed": action_proposed,
                    "decision_outcome": decision_outcome,
                    "ticker": ticker,
                    "votes": votes,
                    "transcript_text": transcript_text,
                    "timestamp_utc": timestamp_utc,
                }
                d_key = (session_id, action_proposed, ticker)
                if d_key in seen_decision_records:
                    prev = seen_decision_records[d_key]
                    if records_equal(prev, dec_rec, DECISION_LOG_EQUALITY_FIELDS):
                        plan.discrepancies.append({
                            "domain": "decision_log",
                            "session_id": session_id,
                            "type": "EXACT_DUPLICATE_DEDUPLICATED",
                            "message": f"Exact duplicate decision log record for {session_id}/{action_proposed}/{ticker} deduplicated.",
                        })
                    else:
                        raise MigrationConflictError(
                            f"Conflicting duplicate decision log record for {session_id}/{action_proposed}/{ticker} in {src_name}: "
                            f"existing {prev} != incoming {dec_rec}"
                        )
                else:
                    seen_decision_records[d_key] = dec_rec
                    plan.decision_log_records.append(dec_rec)

            raw_decisions = data.get("decision_log")
            if isinstance(raw_decisions, list):
                for idx, d in enumerate(raw_decisions):
                    if not isinstance(d, dict):
                        raise MigrationValidationError(f"{src.name}: decision_log[{idx}] must be a dict")

                    # Case A: Canonical structured decision record
                    if "session_id" in d and "action_proposed" in d and "decision_outcome" in d:
                        session_id = str(d["session_id"]).strip()
                        action = str(d["action_proposed"]).strip()
                        outcome = str(d["decision_outcome"]).strip()
                        ticker = parse_ticker(d["ticker"]) if d.get("ticker") else None
                        votes = d.get("votes") or {}
                        transcript = d.get("transcript_text")
                        ts, _ = resolve_historical_timestamp(
                            d.get("timestamp_utc"),
                            f"{src.name}: decision_log[{idx}].timestamp_utc",
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                        _add_decision_record(
                            session_id=session_id,
                            action_proposed=action,
                            decision_outcome=outcome,
                            ticker=ticker,
                            votes=votes,
                            transcript_text=transcript,
                            timestamp_utc=ts,
                            src_name=src.name,
                        )

                    # Case B: Date-based legacy record
                    elif "date" in d and ("lesson" in d or "action" in d or "outcome" in d):
                        trans_b = transform_legacy_decision_case_b(
                            d,
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                        )
                        _add_decision_record(
                            session_id=trans_b["session_id"],
                            action_proposed=trans_b["action_proposed"],
                            decision_outcome=trans_b["decision_outcome"],
                            ticker=trans_b["ticker"],
                            votes=trans_b["votes"],
                            transcript_text=trans_b["transcript_text"],
                            timestamp_utc=trans_b["timestamp_utc"],
                            src_name=src.name,
                        )

                    # Case C: Nested council debate record
                    elif "decisions" in d and isinstance(d["decisions"], list):
                        trans_c_list = transform_legacy_decision_case_c(
                            d,
                            default_timestamp=default_timestamp,
                            allow_naive_utc=allow_naive_utc,
                        )
                        for trans_c in trans_c_list:
                            _add_decision_record(
                                session_id=trans_c["session_id"],
                                action_proposed=trans_c["action_proposed"],
                                decision_outcome=trans_c["decision_outcome"],
                                ticker=trans_c["ticker"],
                                votes=trans_c["votes"],
                                transcript_text=trans_c["transcript_text"],
                                timestamp_utc=trans_c["timestamp_utc"],
                                src_name=src.name,
                            )

                    else:
                        plan.discrepancies.append({
                            "domain": "decision_log",
                            "index": idx,
                            "reason": "HETEROGENEOUS_LEGACY_DECISION_SHAPE",
                            "message": f"Decision log entry {idx} in {src.name} has non-tabular legacy deliberation format; skipped.",
                            "keys": sorted(list(d.keys())),
                        })

        return plan

    def import_into(
        self,
        sources: Union[Dict[str, Any], Sequence[Union[Path, str, MigrationSource]]],
        destination: Union[Path, str, DatabaseManager],
        default_timestamp: Optional[str] = None,
        default_account_id: str = "PRIMARY",
        initialize_schema: bool = True,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> MigrationResult:
        """
        Executes an atomic migration import against an explicitly validated disposable database.
        Guarantees:
        - Rejection of production database destinations.
        - Fail-closed atomicity via UnitOfWork: full rollback on any failure.
        - Idempotency: exact duplicates skipped; conflicting records raise MigrationConflictError.
        """
        safe_dest_path = validate_migration_destination(destination, self._repo_root)

        if default_timestamp is None:
            migration_ts = utc_now_iso()
        else:
            migration_ts = parse_iso_timestamp(default_timestamp, "default_timestamp")

        plan = self.plan_migration(
            sources,
            default_timestamp=default_timestamp,
            default_account_id=default_account_id,
            allow_date_only=allow_date_only,
            allow_naive_utc=allow_naive_utc,
        )

        # Obtain or initialize DatabaseManager
        if isinstance(destination, DatabaseManager):
            db_mgr = destination
        else:
            db_mgr = DatabaseManager(str(safe_dest_path), profile=PRIMARY_FINANCIAL_PROFILE)

        if initialize_schema:
            with db_mgr.connection() as setup_conn:
                SchemaMigrator.initialize_core_schema(setup_conn)

        imported_counts = {
            "portfolio_cash": 0,
            "portfolio_positions": 0,
            "watchlist": 0,
            "scout_categories": 0,
            "trade_lessons": 0,
            "decision_log": 0,
        }
        skipped_counts = {
            "portfolio_cash": 0,
            "portfolio_positions": 0,
            "watchlist": 0,
            "scout_categories": 0,
            "trade_lessons": 0,
            "decision_log": 0,
        }

        # -------------------------------------------------------------------
        # Atomic Import Transaction via UnitOfWork
        # -------------------------------------------------------------------
        with execution_role_context("System"):
            with UnitOfWork(db_mgr) as uow:
                # 1. Cash Balances
                for c in plan.cash_records:
                    existing = uow.cash.get_cash(c["account_id"], c["currency"])
                    if existing is not None:
                        if (
                            existing.amount == c["amount"]
                            and existing.last_updated_at_utc == c["timestamp_utc"]
                        ):
                            skipped_counts["portfolio_cash"] += 1
                        else:
                            raise MigrationConflictError(
                                f"Cash balance conflict for {c['account_id']}/{c['currency']}: "
                                f"existing (amount={existing.amount}, last_updated={existing.last_updated_at_utc}) != "
                                f"incoming (amount={c['amount']}, timestamp={c['timestamp_utc']})"
                            )
                    else:
                        uow.cash.set_cash_balance(
                            account_id=c["account_id"],
                            currency=c["currency"],
                            amount=c["amount"],
                            caller_role="System",
                            timestamp_utc=c["timestamp_utc"],
                        )
                        imported_counts["portfolio_cash"] += 1

                # 2. Portfolio Positions
                for p in plan.position_records:
                    existing_pos = uow.positions.get_position(p["account_id"], p["ticker"])
                    if existing_pos is not None:
                        if (
                            existing_pos.shares == p["shares"]
                            and existing_pos.wac_eur == p["wac_eur"]
                            and existing_pos.wac_usd == p["wac_usd"]
                            and existing_pos.last_updated_at_utc == p["timestamp_utc"]
                        ):
                            skipped_counts["portfolio_positions"] += 1
                        else:
                            raise MigrationConflictError(
                                f"Position conflict for {p['account_id']}/{p['ticker']}: "
                                f"existing (shares={existing_pos.shares}, wac_eur={existing_pos.wac_eur}, "
                                f"wac_usd={existing_pos.wac_usd}, last_updated={existing_pos.last_updated_at_utc}) != "
                                f"incoming (shares={p['shares']}, wac_eur={p['wac_eur']}, "
                                f"wac_usd={p['wac_usd']}, timestamp={p['timestamp_utc']})"
                            )
                    else:
                        uow.positions.upsert_position(
                            account_id=p["account_id"],
                            ticker=p["ticker"],
                            shares=p["shares"],
                            wac_eur=p["wac_eur"],
                            wac_usd=p["wac_usd"],
                            caller_role="System",
                            timestamp_utc=p["timestamp_utc"],
                        )
                        imported_counts["portfolio_positions"] += 1

                # 3. Watchlist
                for w in plan.watchlist_records:
                    existing_wl = uow.watchlist.get_watchlist_item(w["ticker"])
                    if existing_wl is not None:
                        if (
                            existing_wl.status == w["status"]
                            and existing_wl.notes == w["notes"]
                            and existing_wl.added_at_utc == w["timestamp_utc"]
                        ):
                            skipped_counts["watchlist"] += 1
                        else:
                            raise MigrationConflictError(
                                f"Watchlist conflict for {w['ticker']}: "
                                f"existing (status={existing_wl.status}, notes={existing_wl.notes}, added={existing_wl.added_at_utc}) != "
                                f"incoming (status={w['status']}, notes={w['notes']}, timestamp={w['timestamp_utc']})"
                            )
                    else:
                        uow.watchlist.upsert_watchlist_item(
                            ticker=w["ticker"],
                            status=w["status"],
                            notes=w["notes"],
                            caller_role="System",
                            timestamp_utc=w["timestamp_utc"],
                        )
                        imported_counts["watchlist"] += 1

                # 4. Scout Categories
                for sc in plan.scout_categories:
                    existing_cat = uow.scout_categories.get_category_by_name(sc["category_name"])
                    if existing_cat is not None:
                        if (
                            sorted(list(existing_cat.tickers)) == sorted(list(sc["tickers"]))
                            and existing_cat.description == sc["description"]
                            and existing_cat.updated_at_utc == sc["timestamp_utc"]
                        ):
                            skipped_counts["scout_categories"] += 1
                        else:
                            raise MigrationConflictError(
                                f"Scout category conflict for '{sc['category_name']}': "
                                f"existing (tickers={sorted(list(existing_cat.tickers))}, desc={existing_cat.description}, updated={existing_cat.updated_at_utc}) != "
                                f"incoming (tickers={sorted(list(sc['tickers']))}, desc={sc['description']}, timestamp={sc['timestamp_utc']})"
                            )
                    else:
                        uow.scout_categories.upsert_category(
                            category_name=sc["category_name"],
                            description=sc["description"],
                            tickers=sc["tickers"],
                            caller_role="System",
                            timestamp_utc=sc["timestamp_utc"],
                        )
                        imported_counts["scout_categories"] += 1

                # 5. Trade Lessons
                for tl in plan.trade_lessons:
                    existing_tl = uow.trade_lessons.get_lesson(tl["rule_code"])
                    if existing_tl is not None:
                        if (
                            existing_tl.directive_text == tl["directive_text"]
                            and existing_tl.category == tl["category"]
                            and existing_tl.status == tl["status"]
                            and existing_tl.created_at_utc == tl["created_at_utc"]
                            and existing_tl.last_reviewed_at_utc == tl["last_reviewed_at_utc"]
                        ):
                            skipped_counts["trade_lessons"] += 1
                        else:
                            raise MigrationConflictError(
                                f"Trade lesson conflict for '{tl['rule_code']}': "
                                f"existing (directive='{existing_tl.directive_text}', cat={existing_tl.category}, "
                                f"status={existing_tl.status}, created={existing_tl.created_at_utc}, "
                                f"reviewed={existing_tl.last_reviewed_at_utc}) != "
                                f"incoming (directive='{tl['directive_text']}', cat={tl['category']}, "
                                f"status={tl['status']}, created={tl['created_at_utc']}, "
                                f"reviewed={tl['last_reviewed_at_utc']})"
                            )
                    else:
                        uow.trade_lessons.upsert_lesson(
                            rule_code=tl["rule_code"],
                            directive_text=tl["directive_text"],
                            category=tl["category"],
                            status=tl["status"],
                            caller_role="System",
                            timestamp_utc=tl["created_at_utc"],
                        )
                        imported_counts["trade_lessons"] += 1

                # 6. Decision Log
                for dl in plan.decision_log_records:
                    existing_decisions = uow.decision_log.list_decisions(session_id=dl["session_id"])
                    matching_dec = None
                    for ed in existing_decisions:
                        if (
                            ed.action_proposed == dl["action_proposed"]
                            and ed.ticker == dl["ticker"]
                        ):
                            matching_dec = ed
                            break
                    if matching_dec is not None:
                        if (
                            matching_dec.decision_outcome == dl["decision_outcome"]
                            and (matching_dec.votes or {}) == (dl["votes"] or {})
                            and matching_dec.transcript_text == dl["transcript_text"]
                            and matching_dec.timestamp_utc == dl["timestamp_utc"]
                        ):
                            skipped_counts["decision_log"] += 1
                        else:
                            raise MigrationConflictError(
                                f"Decision log conflict for {dl['session_id']}/{dl['action_proposed']}/{dl['ticker']}: "
                                f"existing (outcome={matching_dec.decision_outcome}, ts={matching_dec.timestamp_utc}) != "
                                f"incoming (outcome={dl['decision_outcome']}, ts={dl['timestamp_utc']})"
                            )
                    else:
                        uow.decision_log.append_decision(
                            session_id=dl["session_id"],
                            action_proposed=dl["action_proposed"],
                            decision_outcome=dl["decision_outcome"],
                            ticker=dl["ticker"],
                            votes=dl["votes"],
                            transcript_text=dl["transcript_text"],
                            caller_role="System",
                            timestamp_utc=dl["timestamp_utc"],
                        )
                        imported_counts["decision_log"] += 1

        # Inspect resulting revision
        with db_mgr.connection() as verify_conn:
            final_rev = get_db_revision(verify_conn)

        return MigrationResult(
            success=True,
            database_revision=final_rev,
            imported_counts=imported_counts,
            skipped_counts=skipped_counts,
            discrepancies=plan.discrepancies,
            migration_timestamp_utc=migration_ts,
            destination_path=str(safe_dest_path),
        )
