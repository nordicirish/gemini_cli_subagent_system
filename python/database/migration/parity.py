"""
SSoT Parity Verifier.
Governed by Stage 1.5-D Architecture and Agent Governance Framework.
"""

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
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
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
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
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
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
        """Compares cash amounts using exact Decimal math."""
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

    def _verify_positions_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares portfolio positions using exact Decimal share and cost basis checks."""
        leg_positions = (
            legacy.get("portfolio_snapshot")
            or (legacy.get("mutable_state", {}).get("portfolio_snapshot") if isinstance(legacy.get("mutable_state"), dict) else None)
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
                        ))
                except (InvalidOperation, ValueError):
                    summary.unresolved += 1

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
                ))

    def _verify_watchlist_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares watched tickers and watchlist records."""
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
                    ))

    def _verify_trade_lessons_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares trade lessons across rule codes and directives."""
        leg_lessons = legacy.get("trade_lessons", [])
        proj_lessons = projected.get("trade_lessons", [])
        proj_map = {l.get("rule_code"): l for l in proj_lessons if isinstance(l, dict) and l.get("rule_code")}

        for item in leg_lessons:
            if not isinstance(item, dict):
                continue
            rule_code = item.get("rule_code")
            if not rule_code and "rule" in item:
                rule_code, _ = extract_rule_code_and_directive(str(item["rule"]))

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
                ))
                continue

            if rule_code in proj_map:
                summary.exact_matches += 1
            else:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{rule_code}]",
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=rule_code,
                    projected_value=None,
                    message=f"Trade lesson {rule_code} present in legacy source is missing in projection",
                ))

    def _verify_decision_log_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
    ) -> None:
        """Compares decision log entries."""
        leg_decisions = legacy.get("decision_log", [])
        proj_decisions = projected.get("decision_log", [])
        proj_sessions = {d.get("session_id"): d for d in proj_decisions if isinstance(d, dict) and d.get("session_id")}

        for idx, d in enumerate(leg_decisions):
            if not isinstance(d, dict):
                continue
            session_id = d.get("session_id")
            if session_id and session_id in proj_sessions:
                summary.exact_matches += 1
            else:
                if not session_id:
                    summary.unresolved += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="decision_log",
                        path=f"decision_log[{idx}]",
                        discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                        legacy_value=list(d.keys()),
                        projected_value=None,
                        message=f"Legacy decision log entry {idx} has heterogeneous structure without session_id",
                    ))
                else:
                    summary.missing_records += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="decision_log",
                        path=f"decision_log[{session_id}]",
                        discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                        legacy_value=session_id,
                        projected_value=None,
                        message=f"Decision log session {session_id} missing in projection",
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
