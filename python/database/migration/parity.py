"""
SSoT Parity Verifier.
Governed by Stage 1.5-D Architecture and Agent Governance Framework.
"""

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from python.database.projections import (
    SSoTProjectionGenerator,
    SQLITE_OWNED_TOP_LEVEL_KEYS,
    SQLITE_OWNED_MUTABLE_STATE_KEYS,
)
from python.database.migration.models import (
    DiscrepancyType,
    ParityDiscrepancy,
    ParitySummary,
    ParityResult,
    MigrationSource,
)
from python.database.migration.contract import (
    EXTERNALLY_MANAGED_SECTIONS,
    extract_rule_code_and_directive,
    load_strict_json_file,
)

__all__ = ["SSoTParityVerifier"]


class SSoTParityVerifier:
    """
    Semantic, ownership-aware parity verifier comparing legacy JSON state against
    fresh SQLite projections generated via SSoTProjectionGenerator.
    """

    def __init__(self, generator: Optional[SSoTProjectionGenerator] = None):
        self._generator = generator or SSoTProjectionGenerator()

    def compare(
        self,
        legacy_sources: Union[Dict[str, Any], List[Any]],
        projected_document: Dict[str, Any],
        base_document: Optional[Dict[str, Any]] = None,
        source_names: Optional[List[str]] = None,
    ) -> ParityResult:
        """
        Performs semantic domain-by-domain parity verification.
        Distinguishes:
        - Exact matches
        - Equivalent values with expected representation differences (e.g. scale canonicalization)
        - Expected externally managed sections
        - Financial mismatches (zero float tolerance)
        - Missing or unexpected records
        - Unresolvable legacy discrepancies
        """
        summary = ParitySummary()
        discrepancies: List[ParityDiscrepancy] = []

        # Consolidate legacy state across supplied sources
        legacy_state: Dict[str, Any] = {}
        file_list: List[str] = list(source_names or [])

        if isinstance(legacy_sources, MigrationSource):
            if legacy_sources.name not in file_list:
                file_list.append(legacy_sources.name)
            legacy_state.update(legacy_sources.data)
        elif isinstance(legacy_sources, (str, Path)):
            p = Path(legacy_sources)
            if p.name not in file_list:
                file_list.append(p.name)
            data = load_strict_json_file(p)
            legacy_state.update(data)
        elif isinstance(legacy_sources, dict):
            # Check if dict is { "filename": {data} } or direct data dict
            is_file_map = (
                bool(legacy_sources)
                and all(
                    (isinstance(k, str) and (k.endswith(".json") or "/" in k or "\\" in k))
                    for k in legacy_sources.keys()
                )
                and all(isinstance(v, (dict, MigrationSource)) for v in legacy_sources.values())
            )
            if is_file_map:
                for k, v in legacy_sources.items():
                    if k not in file_list:
                        file_list.append(k)
                    if isinstance(v, MigrationSource):
                        legacy_state.update(v.data)
                    elif isinstance(v, dict):
                        legacy_state.update(v)
            else:
                legacy_state.update(legacy_sources)
        elif isinstance(legacy_sources, (list, tuple)):
            for src in legacy_sources:
                if isinstance(src, MigrationSource):
                    if src.name not in file_list:
                        file_list.append(src.name)
                    legacy_state.update(src.data)
                elif isinstance(src, (str, Path)):
                    p = Path(src)
                    if p.name not in file_list:
                        file_list.append(p.name)
                    data = load_strict_json_file(p)
                    legacy_state.update(data)
                elif isinstance(src, dict):
                    legacy_state.update(src)

        db_revision = projected_document.get("_projection_metadata", {}).get("db_revision", 0)

        # -------------------------------------------------------------------
        # 1. Verification: _projection_metadata
        # -------------------------------------------------------------------
        if "_projection_metadata" in projected_document:
            summary.expected_external_sections += 1

        # -------------------------------------------------------------------
        # 2. Verification: Portfolio Cash Domain
        # -------------------------------------------------------------------
        self._verify_cash_domain(legacy_state, projected_document, summary, discrepancies)

        # -------------------------------------------------------------------
        # 3. Verification: Portfolio Positions Domain
        # -------------------------------------------------------------------
        self._verify_positions_domain(legacy_state, projected_document, summary, discrepancies)

        # -------------------------------------------------------------------
        # 4. Verification: Watchlist Domain
        # -------------------------------------------------------------------
        self._verify_watchlist_domain(legacy_state, projected_document, summary, discrepancies)

        # -------------------------------------------------------------------
        # 5. Verification: Scout Categories Domain
        # -------------------------------------------------------------------
        self._verify_scout_categories_domain(legacy_state, projected_document, summary, discrepancies)

        # -------------------------------------------------------------------
        # 6. Verification: Trade Lessons Domain
        # -------------------------------------------------------------------
        self._verify_trade_lessons_domain(legacy_state, projected_document, summary, discrepancies)

        # -------------------------------------------------------------------
        # 7. Verification: Decision Log Domain
        # -------------------------------------------------------------------
        self._verify_decision_log_domain(legacy_state, projected_document, summary, discrepancies)

        # -------------------------------------------------------------------
        # 8. Verification: Externally Managed Sections Preservation
        # -------------------------------------------------------------------
        self._verify_external_sections(legacy_state, projected_document, base_document, summary, discrepancies)

        # Determine overall parity: no critical discrepancies
        is_parity = (
            summary.missing_records == 0
            and summary.unexpected_records == 0
            and summary.financial_mismatches == 0
            and summary.timestamp_mismatches == 0
            and summary.identity_mismatches == 0
            and summary.unresolved == 0
        )

        # Sort discrepancies deterministically by path and domain
        sorted_discrepancies = sorted(discrepancies, key=lambda d: (d.domain, d.path, d.discrepancy_type))

        return ParityResult(
            schema_version="1.0",
            is_parity=is_parity,
            database_revision=db_revision,
            source_files=sorted(file_list),
            summary=summary,
            discrepancies=sorted_discrepancies,
        )

    # -----------------------------------------------------------------------
    # Domain Verifiers
    # -----------------------------------------------------------------------

    def _verify_cash_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares cash amounts using exact Decimal math and validates structured records."""
        # EUR Cash
        leg_eur = (
            legacy.get("unallocated_cash_eur")
            or (legacy.get("mutable_state", {}).get("unallocated_cash_eur") if isinstance(legacy.get("mutable_state"), dict) else None)
        )
        if leg_eur is None and isinstance(legacy.get("portfolio_cash"), list):
            for c in legacy["portfolio_cash"]:
                if isinstance(c, dict) and c.get("currency") == "EUR":
                    leg_eur = c.get("amount")
                    break
        proj_eur = projected.get("unallocated_cash_eur")

        if leg_eur is not None and proj_eur is not None:
            try:
                dec_leg = Decimal(str(leg_eur))
                dec_proj = Decimal(str(proj_eur))
                if dec_leg == dec_proj:
                    if leg_eur == proj_eur:
                        summary.exact_matches += 1
                    else:
                        summary.equivalent_values += 1
                else:
                    summary.financial_mismatches += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="portfolio_cash",
                        path="unallocated_cash_eur",
                        discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                        legacy_value=leg_eur,
                        projected_value=proj_eur,
                        message=f"EUR cash amount mismatch: legacy {dec_leg} != projected {dec_proj}",
                    ))
            except (InvalidOperation, ValueError):
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_cash",
                    path="unallocated_cash_eur",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=leg_eur,
                    projected_value=proj_eur,
                    message="Failed to parse cash as Decimal",
                ))
        elif leg_eur is not None and proj_eur is None:
            summary.missing_records += 1
            discrepancies.append(ParityDiscrepancy(
                domain="portfolio_cash",
                path="unallocated_cash_eur",
                discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                legacy_value=leg_eur,
                projected_value=None,
                message="EUR cash present in legacy source but missing in projection",
            ))

        # USD Cash
        leg_usd = (
            legacy.get("unallocated_cash_usd")
            or (legacy.get("mutable_state", {}).get("unallocated_cash_usd") if isinstance(legacy.get("mutable_state"), dict) else None)
        )
        if leg_usd is None and isinstance(legacy.get("portfolio_cash"), list):
            for c in legacy["portfolio_cash"]:
                if isinstance(c, dict) and c.get("currency") == "USD":
                    leg_usd = c.get("amount")
                    break
        proj_usd = projected.get("unallocated_cash_usd")
        if leg_usd is not None and proj_usd is not None:
            try:
                dec_leg_usd = Decimal(str(leg_usd))
                dec_proj_usd = Decimal(str(proj_usd))
                if dec_leg_usd == dec_proj_usd:
                    if leg_usd == proj_usd:
                        summary.exact_matches += 1
                    else:
                        summary.equivalent_values += 1
                else:
                    summary.financial_mismatches += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="portfolio_cash",
                        path="unallocated_cash_usd",
                        discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                        legacy_value=leg_usd,
                        projected_value=proj_usd,
                        message=f"USD cash amount mismatch: legacy {dec_leg_usd} != projected {dec_proj_usd}",
                    ))
            except (InvalidOperation, ValueError):
                summary.unresolved += 1

        # Structured Portfolio Cash verification
        if isinstance(legacy.get("portfolio_cash"), list):
            proj_cash_list = projected.get("portfolio_cash", [])
            proj_cash_map = {
                (c.get("account_id"), c.get("currency")): c
                for c in proj_cash_list
                if isinstance(c, dict) and c.get("account_id") and c.get("currency")
            }
            for idx, leg_c in enumerate(legacy["portfolio_cash"]):
                if not isinstance(leg_c, dict):
                    continue
                acc = leg_c.get("account_id", "PRIMARY")
                curr = leg_c.get("currency", "EUR")
                key = (acc, curr)
                if key not in proj_cash_map:
                    summary.missing_records += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="portfolio_cash",
                        path=f"portfolio_cash[{acc}/{curr}]",
                        discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                        legacy_value=leg_c,
                        projected_value=None,
                        message=f"Portfolio cash {acc}/{curr} missing in projection",
                        record_id=f"{acc}/{curr}",
                    ))
                else:
                    proj_c = proj_cash_map[key]
                    # Amount
                    try:
                        dec_leg_amt = Decimal(str(leg_c.get("amount")))
                        dec_proj_amt = Decimal(str(proj_c.get("amount")))
                        if dec_leg_amt != dec_proj_amt:
                            summary.financial_mismatches += 1
                            discrepancies.append(ParityDiscrepancy(
                                domain="portfolio_cash",
                                path=f"portfolio_cash[{acc}/{curr}].amount",
                                discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                                legacy_value=leg_c.get("amount"),
                                projected_value=proj_c.get("amount"),
                                message=f"Portfolio cash amount mismatch for {acc}/{curr}: {dec_leg_amt} != {dec_proj_amt}",
                                record_id=f"{acc}/{curr}",
                            ))
                    except (InvalidOperation, ValueError):
                        summary.unresolved += 1

                    # Timestamp
                    if (
                        leg_c.get("last_updated_at_utc")
                        and leg_c.get("last_updated_at_utc") != proj_c.get("last_updated_at_utc")
                    ):
                        summary.timestamp_mismatches += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="portfolio_cash",
                            path=f"portfolio_cash[{acc}/{curr}].last_updated_at_utc",
                            discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                            legacy_value=leg_c.get("last_updated_at_utc"),
                            projected_value=proj_c.get("last_updated_at_utc"),
                            message=f"Portfolio cash timestamp mismatch for {acc}/{curr}",
                            record_id=f"{acc}/{curr}",
                        ))

    def _verify_positions_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares portfolio positions across shares, cost bases (EUR and USD), and metadata."""
        leg_positions = (
            legacy.get("portfolio_snapshot")
            or legacy.get("portfolio_positions")
            or (legacy.get("mutable_state", {}).get("portfolio_snapshot") if isinstance(legacy.get("mutable_state"), dict) else None)
            or (legacy.get("mutable_state", {}).get("portfolio_positions") if isinstance(legacy.get("mutable_state"), dict) else None)
            or []
        )
        proj_positions = projected.get("portfolio_snapshot", [])

        leg_map = {str(p.get("ticker")).strip().upper(): p for p in leg_positions if isinstance(p, dict) and p.get("ticker")}
        proj_map = {str(p.get("ticker")).strip().upper(): p for p in proj_positions if isinstance(p, dict) and p.get("ticker")}

        # Check legacy positions against projected
        for ticker, leg_p in leg_map.items():
            if ticker not in proj_map:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{ticker}]",
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=leg_p,
                    projected_value=None,
                    message=f"Position for ticker {ticker} in legacy source is missing in projection",
                    record_id=ticker,
                ))
                continue

            proj_p = proj_map[ticker]

            # Compare shares
            try:
                dec_leg_shares = Decimal(str(leg_p.get("shares")))
                dec_proj_shares = Decimal(str(proj_p.get("shares")))
                if dec_leg_shares == dec_proj_shares:
                    if leg_p.get("shares") == proj_p.get("shares"):
                        summary.exact_matches += 1
                    else:
                        summary.equivalent_values += 1
                else:
                    summary.financial_mismatches += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="portfolio_positions",
                        path=f"portfolio_snapshot[{ticker}].shares",
                        discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                        legacy_value=leg_p.get("shares"),
                        projected_value=proj_p.get("shares"),
                        message=f"Shares mismatch for {ticker}: {dec_leg_shares} != {dec_proj_shares}",
                        record_id=ticker,
                    ))
            except (InvalidOperation, ValueError):
                summary.unresolved += 1

            # Compare WAC EUR
            leg_wac = leg_p.get("wac_eur") if leg_p.get("wac_eur") is not None else leg_p.get("wac")
            proj_wac = proj_p.get("wac_eur") if proj_p.get("wac_eur") is not None else proj_p.get("wac")
            if leg_wac is not None and proj_wac is not None:
                try:
                    dec_leg_wac = Decimal(str(leg_wac))
                    dec_proj_wac = Decimal(str(proj_wac))
                    if dec_leg_wac == dec_proj_wac:
                        if leg_wac == proj_wac:
                            summary.exact_matches += 1
                        else:
                            summary.equivalent_values += 1
                    else:
                        summary.financial_mismatches += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="portfolio_positions",
                            path=f"portfolio_snapshot[{ticker}].wac_eur",
                            discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                            legacy_value=leg_wac,
                            projected_value=proj_wac,
                            message=f"WAC EUR mismatch for {ticker}: {dec_leg_wac} != {dec_proj_wac}",
                            record_id=ticker,
                        ))
                except (InvalidOperation, ValueError):
                    summary.unresolved += 1

            # Compare WAC USD (D-02)
            leg_wac_usd = leg_p.get("wac_usd")
            proj_wac_usd = proj_p.get("wac_usd")
            if leg_wac_usd is not None or proj_wac_usd is not None:
                if leg_wac_usd is None or proj_wac_usd is None:
                    summary.financial_mismatches += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="portfolio_positions",
                        path=f"portfolio_snapshot[{ticker}].wac_usd",
                        discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                        legacy_value=leg_wac_usd,
                        projected_value=proj_wac_usd,
                        message=f"WAC USD presence mismatch for {ticker}: legacy={leg_wac_usd} != projected={proj_wac_usd}",
                        record_id=ticker,
                    ))
                else:
                    try:
                        dec_leg_usd = Decimal(str(leg_wac_usd))
                        dec_proj_usd = Decimal(str(proj_wac_usd))
                        if dec_leg_usd == dec_proj_usd:
                            if leg_wac_usd == proj_wac_usd:
                                summary.exact_matches += 1
                            else:
                                summary.equivalent_values += 1
                        else:
                            summary.financial_mismatches += 1
                            discrepancies.append(ParityDiscrepancy(
                                domain="portfolio_positions",
                                path=f"portfolio_snapshot[{ticker}].wac_usd",
                                discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                                legacy_value=leg_wac_usd,
                                projected_value=proj_wac_usd,
                                message=f"WAC USD mismatch for {ticker}: {dec_leg_usd} != {dec_proj_usd}",
                                record_id=ticker,
                            ))
                    except (InvalidOperation, ValueError):
                        summary.unresolved += 1

            # Compare Account ID (D-02)
            if leg_p.get("account_id") and leg_p.get("account_id") != proj_p.get("account_id"):
                summary.identity_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{ticker}].account_id",
                    discrepancy_type=DiscrepancyType.IDENTITY_MISMATCH.value,
                    legacy_value=leg_p.get("account_id"),
                    projected_value=proj_p.get("account_id"),
                    message=f"Account ID mismatch for position {ticker}",
                    record_id=ticker,
                ))

            # Compare Timestamp (D-02)
            if (
                leg_p.get("last_updated_at_utc")
                and leg_p.get("last_updated_at_utc") != proj_p.get("last_updated_at_utc")
            ):
                summary.timestamp_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{ticker}].last_updated_at_utc",
                    discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                    legacy_value=leg_p.get("last_updated_at_utc"),
                    projected_value=proj_p.get("last_updated_at_utc"),
                    message=f"Timestamp mismatch for position {ticker}",
                    record_id=ticker,
                ))

        # Check for unexpected projected positions
        for ticker, proj_p in proj_map.items():
            if ticker not in leg_map:
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{ticker}]",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=proj_p,
                    message=f"Unexpected position for ticker {ticker} present in projection but absent from legacy source",
                    record_id=ticker,
                ))

    def _verify_watchlist_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares watched tickers and structured watchlist records."""
        leg_wl = (
            legacy.get("watched_tickers")
            or (legacy.get("mutable_state", {}).get("watched_tickers") if isinstance(legacy.get("mutable_state"), dict) else None)
            or legacy.get("WATCHLIST")
            or []
        )
        if isinstance(leg_wl, list) and leg_wl and isinstance(leg_wl[0], dict):
            leg_set = {str(item.get("ticker")).strip().upper() for item in leg_wl if item.get("ticker")}
        else:
            leg_set = {str(t).strip().upper() for t in leg_wl if t}

        proj_wl = projected.get("watched_tickers", [])
        proj_set = {str(t).strip().upper() for t in proj_wl if t}

        if leg_set == proj_set:
            summary.exact_matches += len(leg_set)
        else:
            for missing in (leg_set - proj_set):
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="watchlist",
                    path=f"watched_tickers[{missing}]",
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=missing,
                    projected_value=None,
                    message=f"Watched ticker {missing} in legacy source is missing in projection",
                    record_id=missing,
                ))
            for unexpected in (proj_set - leg_set):
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="watchlist",
                    path=f"watched_tickers[{unexpected}]",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=unexpected,
                    message=f"Unexpected watched ticker {unexpected} in projection",
                    record_id=unexpected,
                ))

        # Structured watchlist comparison (D-02)
        if isinstance(legacy.get("watchlist"), list) and legacy["watchlist"] and isinstance(legacy["watchlist"][0], dict):
            proj_items = {
                item.get("ticker"): item
                for item in projected.get("watchlist", [])
                if isinstance(item, dict) and item.get("ticker")
            }
            for leg_item in legacy["watchlist"]:
                if not isinstance(leg_item, dict):
                    continue
                tk = leg_item.get("ticker")
                if tk in proj_items:
                    proj_item = proj_items[tk]
                    if leg_item.get("status") and leg_item.get("status") != proj_item.get("status"):
                        summary.unresolved += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="watchlist",
                            path=f"watchlist[{tk}].status",
                            discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                            legacy_value=leg_item.get("status"),
                            projected_value=proj_item.get("status"),
                            message=f"Status mismatch for watchlist item {tk}",
                            record_id=tk,
                        ))
                    if leg_item.get("notes") != proj_item.get("notes"):
                        summary.unresolved += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="watchlist",
                            path=f"watchlist[{tk}].notes",
                            discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                            legacy_value=leg_item.get("notes"),
                            projected_value=proj_item.get("notes"),
                            message=f"Notes mismatch for watchlist item {tk}",
                            record_id=tk,
                        ))
                    if (
                        leg_item.get("added_at_utc")
                        and leg_item.get("added_at_utc") != proj_item.get("added_at_utc")
                    ):
                        summary.timestamp_mismatches += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="watchlist",
                            path=f"watchlist[{tk}].added_at_utc",
                            discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                            legacy_value=leg_item.get("added_at_utc"),
                            projected_value=proj_item.get("added_at_utc"),
                            message=f"Timestamp mismatch for watchlist item {tk}",
                            record_id=tk,
                        ))

    def _verify_scout_categories_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares scout categories and ticker mappings."""
        leg_map: Dict[str, Set[str]] = {}
        if "SCOUT_TICKER_MAP" in legacy and isinstance(legacy["SCOUT_TICKER_MAP"], dict):
            leg_map = {k: set(v) for k, v in legacy["SCOUT_TICKER_MAP"].items()}
        elif "scout_category_records" in legacy and isinstance(legacy["scout_category_records"], list):
            leg_map = {r.get("category_name"): set(r.get("tickers", [])) for r in legacy["scout_category_records"]}

        proj_records = projected.get("scout_category_records", [])
        proj_map = {r.get("category_name"): set(r.get("tickers", [])) for r in proj_records if isinstance(r, dict)}
        proj_cat_objs = {r.get("category_name"): r for r in proj_records if isinstance(r, dict)}

        for cat_name, leg_tickers in leg_map.items():
            if cat_name not in proj_map:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="scout_categories",
                    path=f"scout_categories[{cat_name}]",
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=sorted(list(leg_tickers)),
                    projected_value=None,
                    message=f"Scout category '{cat_name}' missing in projection",
                    record_id=cat_name,
                ))
            else:
                proj_tickers = proj_map[cat_name]
                if leg_tickers == proj_tickers:
                    summary.exact_matches += 1
                else:
                    summary.unresolved += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="scout_categories",
                        path=f"scout_categories[{cat_name}].tickers",
                        discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                        legacy_value=sorted(list(leg_tickers)),
                        projected_value=sorted(list(proj_tickers)),
                        message=f"Tickers mismatch for scout category '{cat_name}'",
                        record_id=cat_name,
                    ))

        # Structured scout categories comparison (D-02)
        leg_structured = legacy.get("scout_category_records") or legacy.get("scout_categories")
        if isinstance(leg_structured, list) and leg_structured and isinstance(leg_structured[0], dict):
            for sc in leg_structured:
                if not isinstance(sc, dict):
                    continue
                cn = sc.get("category_name")
                if cn in proj_cat_objs:
                    proj_sc = proj_cat_objs[cn]
                    if sc.get("description") != proj_sc.get("description"):
                        summary.unresolved += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="scout_categories",
                            path=f"scout_categories[{cn}].description",
                            discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                            legacy_value=sc.get("description"),
                            projected_value=proj_sc.get("description"),
                            message=f"Description mismatch for scout category '{cn}'",
                            record_id=cn,
                        ))
                    if (
                        sc.get("updated_at_utc")
                        and sc.get("updated_at_utc") != proj_sc.get("updated_at_utc")
                    ):
                        summary.timestamp_mismatches += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="scout_categories",
                            path=f"scout_categories[{cn}].updated_at_utc",
                            discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                            legacy_value=sc.get("updated_at_utc"),
                            projected_value=proj_sc.get("updated_at_utc"),
                            message=f"Timestamp mismatch for scout category '{cn}'",
                            record_id=cn,
                        ))

    def _verify_trade_lessons_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares trade lessons across rule codes, directives, categories, and timestamps (D-02)."""
        leg_lessons = legacy.get("trade_lessons", [])
        proj_lessons = projected.get("trade_lessons", [])
        proj_map = {l.get("rule_code"): l for l in proj_lessons if isinstance(l, dict) and l.get("rule_code")}

        for item in leg_lessons:
            if not isinstance(item, dict):
                continue
            rule_code = item.get("rule_code")
            leg_directive = item.get("directive_text")
            if not rule_code and "rule" in item:
                rule_code, extracted_dir = extract_rule_code_and_directive(str(item["rule"]))
                if not leg_directive:
                    leg_directive = extracted_dir or str(item["rule"])

            if rule_code is None:
                # Documented legacy limitation (e.g. items 16/17 without rule code)
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[id={item.get('id')}]",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=item.get("rule", "")[:40],
                    projected_value=None,
                    message="Legacy trade lesson has uncodified format without 'L-xxx' prefix; not importable.",
                    record_id=str(item.get("id")),
                ))
                continue

            if rule_code not in proj_map:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{rule_code}]",
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=rule_code,
                    projected_value=None,
                    message=f"Trade lesson {rule_code} present in legacy source is missing in projection",
                    record_id=rule_code,
                ))
                continue

            proj_l = proj_map[rule_code]
            has_mismatch = False

            # Verify directive text (D-02)
            if leg_directive and leg_directive != proj_l.get("directive_text"):
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{rule_code}].directive_text",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=leg_directive,
                    projected_value=proj_l.get("directive_text"),
                    message=f"Directive text mismatch for trade lesson {rule_code}",
                    record_id=rule_code,
                ))
                has_mismatch = True

            # Verify category (D-02)
            if item.get("category") and item.get("category") != proj_l.get("category"):
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{rule_code}].category",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=item.get("category"),
                    projected_value=proj_l.get("category"),
                    message=f"Category mismatch for trade lesson {rule_code}",
                    record_id=rule_code,
                ))
                has_mismatch = True

            # Verify status (D-02)
            if item.get("status") and item.get("status") != proj_l.get("status"):
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{rule_code}].status",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=item.get("status"),
                    projected_value=proj_l.get("status"),
                    message=f"Status mismatch for trade lesson {rule_code}",
                    record_id=rule_code,
                ))
                has_mismatch = True

            # Verify created_at_utc (D-02)
            if item.get("created_at_utc") and item.get("created_at_utc") != proj_l.get("created_at_utc"):
                summary.timestamp_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{rule_code}].created_at_utc",
                    discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                    legacy_value=item.get("created_at_utc"),
                    projected_value=proj_l.get("created_at_utc"),
                    message=f"Created timestamp mismatch for trade lesson {rule_code}",
                    record_id=rule_code,
                ))
                has_mismatch = True

            # Verify last_reviewed_at_utc (D-02)
            if (
                item.get("last_reviewed_at_utc")
                and item.get("last_reviewed_at_utc") != proj_l.get("last_reviewed_at_utc")
            ):
                summary.timestamp_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{rule_code}].last_reviewed_at_utc",
                    discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                    legacy_value=item.get("last_reviewed_at_utc"),
                    projected_value=proj_l.get("last_reviewed_at_utc"),
                    message=f"Last reviewed timestamp mismatch for trade lesson {rule_code}",
                    record_id=rule_code,
                ))
                has_mismatch = True

            if not has_mismatch:
                summary.exact_matches += 1

    def _verify_decision_log_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares decision log entries with field-level vote, transcript, and outcome verification (D-02, D-06)."""
        leg_decisions = legacy.get("decision_log", [])
        proj_decisions = projected.get("decision_log", [])
        proj_by_session: Dict[str, List[Dict[str, Any]]] = {}
        for d in proj_decisions:
            if isinstance(d, dict) and d.get("session_id"):
                proj_by_session.setdefault(d["session_id"], []).append(d)

        for idx, d in enumerate(leg_decisions):
            if not isinstance(d, dict):
                continue

            # Case A: Canonical structured decision record
            if "session_id" in d and "action_proposed" in d and "decision_outcome" in d:
                session_id = str(d["session_id"]).strip()
                action = str(d["action_proposed"]).strip()
                outcome = str(d["decision_outcome"]).strip()
                ticker = str(d.get("ticker")).strip().upper() if d.get("ticker") else None

                candidates = proj_by_session.get(session_id, [])
                matching = None
                for c in candidates:
                    if c.get("action_proposed") == action and c.get("ticker") == ticker:
                        matching = c
                        break

                if not matching:
                    summary.missing_records += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="decision_log",
                        path=f"decision_log[{session_id}/{action}/{ticker}]",
                        discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                        legacy_value=d,
                        projected_value=None,
                        message=f"Decision log entry {session_id}/{action} missing in projection",
                        record_id=session_id,
                    ))
                else:
                    has_mismatch = False
                    if matching.get("decision_outcome") != outcome:
                        summary.unresolved += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="decision_log",
                            path=f"decision_log[{session_id}].decision_outcome",
                            discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                            legacy_value=outcome,
                            projected_value=matching.get("decision_outcome"),
                            message=f"Outcome mismatch for decision {session_id}",
                            record_id=session_id,
                        ))
                        has_mismatch = True

                    # Verify votes dict (D-02)
                    if "votes" in d and (matching.get("votes") or {}) != (d.get("votes") or {}):
                        summary.unresolved += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="decision_log",
                            path=f"decision_log[{session_id}].votes",
                            discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                            legacy_value=d.get("votes"),
                            projected_value=matching.get("votes"),
                            message=f"Votes mismatch for decision {session_id}",
                            record_id=session_id,
                        ))
                        has_mismatch = True

                    # Verify transcript text (D-02)
                    if "transcript_text" in d and matching.get("transcript_text") != d.get("transcript_text"):
                        summary.unresolved += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="decision_log",
                            path=f"decision_log[{session_id}].transcript_text",
                            discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                            legacy_value=d.get("transcript_text"),
                            projected_value=matching.get("transcript_text"),
                            message=f"Transcript text mismatch for decision {session_id}",
                            record_id=session_id,
                        ))
                        has_mismatch = True

                    # Verify timestamp
                    if "timestamp_utc" in d and matching.get("timestamp_utc") != d.get("timestamp_utc"):
                        summary.timestamp_mismatches += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="decision_log",
                            path=f"decision_log[{session_id}].timestamp_utc",
                            discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                            legacy_value=d.get("timestamp_utc"),
                            projected_value=matching.get("timestamp_utc"),
                            message=f"Timestamp mismatch for decision {session_id}",
                            record_id=session_id,
                        ))
                        has_mismatch = True

                    if not has_mismatch:
                        summary.exact_matches += 1

            # Case B: Date-based legacy record (D-06)
            elif "date" in d and ("lesson" in d or "action" in d or "outcome" in d):
                date_str = str(d["date"]).strip()
                expected_sid = f"LEGACY-SESSION-{date_str}"
                candidates = proj_by_session.get(expected_sid, [])
                if not candidates:
                    summary.missing_records += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="decision_log",
                        path=f"decision_log[{expected_sid}]",
                        discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                        legacy_value=d,
                        projected_value=None,
                        message=f"Legacy date-based decision log {expected_sid} missing in projection",
                        record_id=expected_sid,
                    ))
                else:
                    matching = candidates[0]
                    expected_outcome = str(d.get("outcome") or "AUTO_LOGGED").strip()
                    if matching.get("decision_outcome") != expected_outcome:
                        summary.unresolved += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="decision_log",
                            path=f"decision_log[{expected_sid}].decision_outcome",
                            discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                            legacy_value=expected_outcome,
                            projected_value=matching.get("decision_outcome"),
                            message=f"Outcome mismatch for legacy decision {expected_sid}",
                            record_id=expected_sid,
                        ))
                    else:
                        summary.exact_matches += 1

            # Case C: Nested council debate record (D-06)
            elif "decisions" in d and isinstance(d["decisions"], list):
                ts = str(d.get("timestamp") or "")
                date_part = ts[:10] if ts else "UNKNOWN"
                trigger_ctx = str(d.get("trigger_context") or "DELIBERATION").strip()
                sanitized_trigger = re.sub(r'[^A-Za-z0-9_]+', '_', trigger_ctx).strip('_')
                expected_sid = f"SESSION-{date_part}-{sanitized_trigger}"
                candidates = proj_by_session.get(expected_sid, [])

                for sub_d in d["decisions"]:
                    if not isinstance(sub_d, dict):
                        continue
                    sub_ticker = str(sub_d.get("ticker")).strip().upper() if sub_d.get("ticker") else None
                    sub_matching = None
                    for c in candidates:
                        if c.get("ticker") == sub_ticker:
                            sub_matching = c
                            break
                    if not sub_matching:
                        summary.missing_records += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="decision_log",
                            path=f"decision_log[{expected_sid}/{sub_ticker}]",
                            discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                            legacy_value=sub_d,
                            projected_value=None,
                            message=f"Sub-decision for {sub_ticker} in {expected_sid} missing in projection",
                            record_id=f"{expected_sid}/{sub_ticker}",
                        ))
                    else:
                        exp_action = str(sub_d.get("trade_state") or sub_d.get("action") or trigger_ctx).strip()
                        exp_outcome = str(sub_d.get("trade_state") or sub_d.get("outcome") or "APPROVED").strip()
                        has_mismatch = False
                        if sub_matching.get("action_proposed") != exp_action:
                            summary.unresolved += 1
                            discrepancies.append(ParityDiscrepancy(
                                domain="decision_log",
                                path=f"decision_log[{expected_sid}/{sub_ticker}].action_proposed",
                                discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                                legacy_value=exp_action,
                                projected_value=sub_matching.get("action_proposed"),
                                message=f"Action mismatch for {expected_sid}/{sub_ticker}",
                                record_id=f"{expected_sid}/{sub_ticker}",
                            ))
                            has_mismatch = True
                        if sub_matching.get("decision_outcome") != exp_outcome:
                            summary.unresolved += 1
                            discrepancies.append(ParityDiscrepancy(
                                domain="decision_log",
                                path=f"decision_log[{expected_sid}/{sub_ticker}].decision_outcome",
                                discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                                legacy_value=exp_outcome,
                                projected_value=sub_matching.get("decision_outcome"),
                                message=f"Outcome mismatch for {expected_sid}/{sub_ticker}",
                                record_id=f"{expected_sid}/{sub_ticker}",
                            ))
                            has_mismatch = True
                        if not has_mismatch:
                            summary.exact_matches += 1

            else:
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="decision_log",
                    path=f"decision_log[{idx}]",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=list(d.keys()),
                    projected_value=None,
                    message=f"Legacy decision log entry {idx} has unrecognized structure",
                    record_id=str(idx),
                ))

    def _verify_external_sections(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        base_document: Optional[Dict[str, Any]],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Verifies that non-tabular / externally managed sections are accounted for."""
        for ext_key in EXTERNALLY_MANAGED_SECTIONS:
            in_base = base_document is not None and ext_key in base_document
            in_leg = ext_key in legacy
            in_proj = ext_key in projected

            if in_proj or in_base or in_leg:
                summary.expected_external_sections += 1
                if in_base and not in_proj:
                    summary.missing_records += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="external_sections",
                        path=ext_key,
                        discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                        legacy_value=base_document.get(ext_key),
                        projected_value=None,
                        message=f"Externally managed section {ext_key} was not preserved in projection",
                    ))
                elif in_base and in_proj:
                    if base_document[ext_key] == projected[ext_key]:
                        summary.exact_matches += 1
                    else:
                        summary.unresolved += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="external_sections",
                            path=ext_key,
                            discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                            legacy_value=base_document.get(ext_key),
                            projected_value=projected.get(ext_key),
                            message=f"Externally managed section {ext_key} modified during projection",
                        ))
