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
    MigrationConflictError,
    MigrationValidationError,
)
from python.database.migration.contract import (
    EXTERNALLY_MANAGED_SECTIONS,
    extract_rule_code_and_directive,
    load_strict_json_file,
    transform_legacy_decision_case_b,
    transform_legacy_decision_case_c,
    parse_ticker,
    resolve_historical_timestamp,
    deep_equal,
    consolidate_scout_representations,
    consolidate_watchlist_representations,
    consolidate_position_representations,
    consolidate_cash_representations,
    _MISSING,
)

__all__ = ["SSoTParityVerifier"]


class SSoTParityVerifier:
    """
    Semantic, ownership-aware parity verifier comparing legacy JSON state against
    fresh SQLite projections generated via SSoTProjectionGenerator.
    """

    def __init__(
        self,
        generator: Optional[SSoTProjectionGenerator] = None,
        default_timestamp: Optional[str] = None,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ):
        self._generator = generator or SSoTProjectionGenerator()
        self.default_timestamp = default_timestamp
        self._allow_date_only = allow_date_only
        self._allow_naive_utc = allow_naive_utc

    def compare(
        self,
        legacy_sources: Union[Dict[str, Any], List[Any]],
        projected_document: Dict[str, Any],
        base_document: Optional[Dict[str, Any]] = None,
        source_names: Optional[List[str]] = None,
        default_timestamp: Optional[str] = None,
        allow_date_only: Optional[bool] = None,
        allow_naive_utc: Optional[bool] = None,
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
        eff_default_ts = default_timestamp if default_timestamp is not None else self.default_timestamp
        eff_allow_date_only = self._allow_date_only if allow_date_only is None else allow_date_only
        eff_allow_naive_utc = self._allow_naive_utc if allow_naive_utc is None else allow_naive_utc
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
        self._verify_cash_domain(
            legacy_state,
            projected_document,
            summary,
            discrepancies,
            default_timestamp=eff_default_ts,
            allow_date_only=eff_allow_date_only,
            allow_naive_utc=eff_allow_naive_utc,
        )

        # -------------------------------------------------------------------
        # 3. Verification: Portfolio Positions Domain
        # -------------------------------------------------------------------
        self._verify_positions_domain(
            legacy_state,
            projected_document,
            summary,
            discrepancies,
            default_timestamp=eff_default_ts,
            allow_date_only=eff_allow_date_only,
            allow_naive_utc=eff_allow_naive_utc,
        )

        # -------------------------------------------------------------------
        # 4. Verification: Watchlist Domain
        # -------------------------------------------------------------------
        self._verify_watchlist_domain(
            legacy_state,
            projected_document,
            summary,
            discrepancies,
            default_timestamp=eff_default_ts,
            allow_date_only=eff_allow_date_only,
            allow_naive_utc=eff_allow_naive_utc,
        )

        # -------------------------------------------------------------------
        # 5. Verification: Scout Categories Domain
        # -------------------------------------------------------------------
        self._verify_scout_categories_domain(
            legacy_state,
            projected_document,
            summary,
            discrepancies,
            default_timestamp=eff_default_ts,
            allow_date_only=eff_allow_date_only,
            allow_naive_utc=eff_allow_naive_utc,
        )

        # -------------------------------------------------------------------
        # 6. Verification: Trade Lessons Domain
        # -------------------------------------------------------------------
        self._verify_trade_lessons_domain(
            legacy_state,
            projected_document,
            summary,
            discrepancies,
            default_timestamp=eff_default_ts,
            allow_date_only=eff_allow_date_only,
            allow_naive_utc=eff_allow_naive_utc,
        )

        # -------------------------------------------------------------------
        # 7. Verification: Decision Log Domain
        # -------------------------------------------------------------------
        self._verify_decision_log_domain(
            legacy_state,
            projected_document,
            summary,
            discrepancies,
            default_timestamp=eff_default_ts,
            allow_date_only=eff_allow_date_only,
            allow_naive_utc=eff_allow_naive_utc,
        )

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
    # Timestamp Field Verification Helper (R1)
    # -----------------------------------------------------------------------

    def _verify_timestamp_field(
        self,
        legacy_val: Any,
        projected_val: Any,
        field_name: str,
        path: str,
        domain: str,
        record_id: Optional[str],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
        nullable: bool = False,
        default_timestamp: Optional[str] = None,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> None:
        """
        Explicitly compares timestamp presence, provenance, and canonical values (R1).
        - Source value present: resolves and normalizes, then checks equality against projection.
        - Source value missing/null:
            - If nullable=True: projected value MUST be None. If non-null, flags TIMESTAMP_MISMATCH.
            - If nullable=False:
                - If default_timestamp is provided: checks projected value == default_timestamp.
                - If default_timestamp is None: flags TIMESTAMP_MISMATCH for unexplained projected timestamp.
        """
        # 1. Source value genuinely missing (_MISSING)
        if legacy_val is _MISSING:
            if nullable:
                if projected_val is None:
                    return
                summary.timestamp_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain=domain,
                    path=path,
                    discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                    legacy_value=None,
                    projected_value=projected_val,
                    message=f"Nullable timestamp {path} expected None in projection, got '{projected_val}'",
                    record_id=record_id,
                ))
                return

            # Required timestamp missing from source: check authorized caller default
            if default_timestamp is not None and str(default_timestamp).strip() != "":
                try:
                    exp_default, _ = resolve_historical_timestamp(
                        _MISSING,
                        field_name,
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                except Exception as e:
                    summary.unresolved += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain=domain,
                        path=path,
                        discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                        legacy_value=None,
                        projected_value=projected_val,
                        message=f"Failed to resolve default timestamp: {e}",
                        record_id=record_id,
                    ))
                    return

                if projected_val == exp_default:
                    summary.equivalent_values += 1
                else:
                    summary.timestamp_mismatches += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain=domain,
                        path=path,
                        discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                        legacy_value=None,
                        projected_value=projected_val,
                        message=f"Timestamp {path} expected caller default '{exp_default}', got '{projected_val}'",
                        record_id=record_id,
                    ))
                return

            # Unexplained projected timestamp when source missing and no default authorized
            if projected_val is not None:
                summary.timestamp_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain=domain,
                    path=path,
                    discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                    legacy_value=None,
                    projected_value=projected_val,
                    message=f"Unexplained projected timestamp '{projected_val}' for {path} when source is missing and no default provided",
                    record_id=record_id,
                ))
            else:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain=domain,
                    path=path,
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=None,
                    projected_value=None,
                    message=f"Required timestamp {path} missing in both source and projection",
                    record_id=record_id,
                ))
            return

        # 2. Source value explicitly None
        elif legacy_val is None:
            if nullable:
                if projected_val is None:
                    return
                summary.timestamp_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain=domain,
                    path=path,
                    discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                    legacy_value=None,
                    projected_value=projected_val,
                    message=f"Nullable timestamp {path} expected None in projection, got '{projected_val}'",
                    record_id=record_id,
                ))
                return
            else:
                # Explicit null for a non-nullable timestamp must report unresolved/invalid source timestamp,
                # never adopting a caller-supplied default.
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain=domain,
                    path=path,
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=None,
                    projected_value=projected_val,
                    message=f"Explicit null supplied for non-nullable timestamp {path}",
                    record_id=record_id,
                ))
                return

        # 3. Source value explicitly supplied (string, number, collection, etc.)
        else:
            try:
                expected_ts, _ = resolve_historical_timestamp(
                    legacy_val,
                    field_name,
                    nullable=nullable,
                    default_timestamp=default_timestamp,
                    allow_date_only=allow_date_only,
                    allow_naive_utc=allow_naive_utc,
                )
            except Exception as e:
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain=domain,
                    path=path,
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=legacy_val,
                    projected_value=projected_val,
                    message=f"Failed to resolve legacy timestamp: {e}",
                    record_id=record_id,
                ))
                return

            if expected_ts == projected_val:
                pass
            else:
                summary.timestamp_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain=domain,
                    path=path,
                    discrepancy_type=DiscrepancyType.TIMESTAMP_MISMATCH.value,
                    legacy_value=legacy_val,
                    projected_value=projected_val,
                    message=f"Timestamp mismatch for {path}: legacy {expected_ts} != projected {projected_val}",
                    record_id=record_id,
                ))
            return

    # -----------------------------------------------------------------------
    # Domain Verifiers
    # -----------------------------------------------------------------------

    def _verify_cash_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
        default_timestamp: Optional[str] = None,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> None:
        """Compares cash amounts using exact Decimal math and validates structured records (D-02, D1.2, R1, R2, R3)."""
        proj_cash_list = projected.get("portfolio_cash", [])
        proj_cash_map = {
            (c.get("account_id"), c.get("currency")): c
            for c in proj_cash_list
            if isinstance(c, dict) and c.get("account_id") and c.get("currency")
        }

        try:
            consolidated_cash = consolidate_cash_representations(
                legacy,
                default_account_id="PRIMARY",
                default_timestamp=default_timestamp,
                allow_date_only=allow_date_only,
                allow_naive_utc=allow_naive_utc,
                require_timestamp=False,
            )
        except (MigrationConflictError, MigrationValidationError) as exc:
            summary.unresolved += 1
            discrepancies.append(ParityDiscrepancy(
                domain="portfolio_cash",
                path="portfolio_cash",
                discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                legacy_value=None,
                projected_value=None,
                message=f"Conflicting cash representations in legacy source: {exc}",
            ))
            return

        leg_cash_map: Dict[Tuple[str, str], Dict[str, Any]] = {
            (c["account_id"], c["currency"]): c for c in consolidated_cash
        }

        # EUR Cash
        leg_eur_rec = leg_cash_map.get(("PRIMARY", "EUR"))
        leg_eur = leg_eur_rec.get("raw_amount", leg_eur_rec.get("amount")) if leg_eur_rec is not None else None
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
        elif leg_eur is None and proj_eur is not None:
            is_real_unexpected = ("PRIMARY", "EUR") in proj_cash_map or Decimal(str(proj_eur)) != Decimal("0.00")
            if is_real_unexpected:
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_cash",
                    path="unallocated_cash_eur",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=proj_eur,
                    message="EUR cash present in projection but missing in legacy source",
                ))

        # USD Cash
        leg_usd_rec = leg_cash_map.get(("PRIMARY", "USD"))
        leg_usd = leg_usd_rec.get("raw_amount", leg_usd_rec.get("amount")) if leg_usd_rec is not None else None
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
        elif leg_usd is not None and proj_usd is None:
            summary.missing_records += 1
            discrepancies.append(ParityDiscrepancy(
                domain="portfolio_cash",
                path="unallocated_cash_usd",
                discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                legacy_value=leg_usd,
                projected_value=None,
                message="USD cash present in legacy source but missing in projection",
            ))
        elif leg_usd is None and proj_usd is not None:
            is_real_unexpected = ("PRIMARY", "USD") in proj_cash_map or Decimal(str(proj_usd)) != Decimal("0.00")
            if is_real_unexpected:
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_cash",
                    path="unallocated_cash_usd",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=proj_usd,
                    message="USD cash present in projection but missing in legacy source",
                ))

        # Structured Portfolio Cash verification
        has_structured_cash = (
            ("portfolio_cash" in legacy and isinstance(legacy["portfolio_cash"], list))
            or (isinstance(legacy.get("mutable_state"), dict) and isinstance(legacy["mutable_state"].get("portfolio_cash"), list))
        )
        if has_structured_cash:
            for key, leg_c in leg_cash_map.items():
                acc, curr = key
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
                        else:
                            raw_c_amt = leg_c.get("raw_amount", leg_c.get("amount"))
                            if raw_c_amt == proj_c.get("amount"):
                                summary.exact_matches += 1
                            else:
                                summary.equivalent_values += 1
                    except (InvalidOperation, ValueError):
                        summary.unresolved += 1

                    # Timestamp (R1)
                    raw_c_ts = leg_c.get("last_updated_at_utc", _MISSING)
                    if raw_c_ts is _MISSING:
                        raw_c_ts = leg_c.get("timestamp_utc", _MISSING)
                    self._verify_timestamp_field(
                        raw_c_ts,
                        proj_c.get("last_updated_at_utc"),
                        "last_updated_at_utc",
                        f"portfolio_cash[{acc}/{curr}].last_updated_at_utc",
                        "portfolio_cash",
                        f"{acc}/{curr}",
                        summary,
                        discrepancies,
                        nullable=False,
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )

        # Reverse check for structured portfolio cash (R5)
        for key, proj_c in proj_cash_map.items():
            if key not in leg_cash_map:
                acc, curr = key
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_cash",
                    path=f"portfolio_cash[{acc}/{curr}]",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=proj_c,
                    message=f"Unexpected portfolio cash {acc}/{curr} present in projection",
                    record_id=f"{acc}/{curr}",
                ))

    def _verify_positions_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
        default_timestamp: Optional[str] = None,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> None:
        """Compares portfolio snapshot positions using composite (account_id, ticker) key (D-02, D1.2, R1, R2, R3)."""
        try:
            consolidated_positions = consolidate_position_representations(
                legacy,
                default_account_id="PRIMARY",
                default_timestamp=default_timestamp,
                allow_date_only=allow_date_only,
                allow_naive_utc=allow_naive_utc,
                require_timestamp=False,
            )
        except (MigrationConflictError, MigrationValidationError) as exc:
            summary.unresolved += 1
            discrepancies.append(ParityDiscrepancy(
                domain="portfolio_positions",
                path="portfolio_snapshot",
                discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                legacy_value=None,
                projected_value=None,
                message=f"Conflicting position representations in legacy source: {exc}",
            ))
            return

        leg_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for p in consolidated_positions:
            raw_acc = p.get("account_id", _MISSING)
            acc = "PRIMARY" if raw_acc is _MISSING else str(raw_acc).strip().upper()
            norm_tk = str(p["ticker"]).strip().upper()
            leg_map[(acc, norm_tk)] = p

        proj_positions = projected.get("portfolio_snapshot", [])
        proj_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for p in proj_positions:
            if isinstance(p, dict) and p.get("ticker"):
                raw_acc = p.get("account_id", _MISSING)
                acc = "PRIMARY" if raw_acc is _MISSING else str(raw_acc).strip().upper()
                norm_tk = str(p["ticker"]).strip().upper()
                proj_map[(acc, norm_tk)] = p

        for (acc, ticker), leg_p in leg_map.items():
            pos_id = ticker if acc == "PRIMARY" else f"{acc}/{ticker}"
            if (acc, ticker) not in proj_map:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{pos_id}]",
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=leg_p,
                    projected_value=None,
                    message=f"Position for {pos_id} in legacy source is missing in projection",
                    record_id=pos_id,
                ))
                continue

            proj_p = proj_map[(acc, ticker)]
            has_mismatch = False

            # Compare shares
            try:
                dec_leg_shares = Decimal(str(leg_p.get("shares")))
                dec_proj_shares = Decimal(str(proj_p.get("shares")))
                if dec_leg_shares == dec_proj_shares:
                    raw_shares = leg_p.get("raw_shares", leg_p.get("shares"))
                    if raw_shares == proj_p.get("shares"):
                        summary.exact_matches += 1
                    else:
                        summary.equivalent_values += 1
                else:
                    has_mismatch = True
                    summary.financial_mismatches += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="portfolio_positions",
                        path=f"portfolio_snapshot[{pos_id}].shares",
                        discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                        legacy_value=leg_p.get("shares"),
                        projected_value=proj_p.get("shares"),
                        message=f"Shares mismatch for {pos_id}: {dec_leg_shares} != {dec_proj_shares}",
                        record_id=pos_id,
                    ))
            except (InvalidOperation, ValueError):
                has_mismatch = True
                summary.unresolved += 1

            # Compare WAC EUR (R2: explicit presence and value equality)
            leg_wac = leg_p.get("wac_eur") if leg_p.get("wac_eur") is not None else leg_p.get("wac")
            proj_wac = proj_p.get("wac_eur") if proj_p.get("wac_eur") is not None else proj_p.get("wac")
            if (leg_wac is None) != (proj_wac is None):
                has_mismatch = True
                summary.financial_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{pos_id}].wac_eur",
                    discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                    legacy_value=leg_wac,
                    projected_value=proj_wac,
                    message=f"WAC EUR presence mismatch for {pos_id}: legacy={leg_wac} != projected={proj_wac}",
                    record_id=pos_id,
                ))
            elif leg_wac is not None:
                try:
                    dec_leg_wac = Decimal(str(leg_wac))
                    dec_proj_wac = Decimal(str(proj_wac))
                    if dec_leg_wac == dec_proj_wac:
                        raw_wac = leg_p.get("raw_wac", leg_wac)
                        if raw_wac == proj_wac:
                            summary.exact_matches += 1
                        else:
                            summary.equivalent_values += 1
                    else:
                        has_mismatch = True
                        summary.financial_mismatches += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="portfolio_positions",
                            path=f"portfolio_snapshot[{pos_id}].wac_eur",
                            discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                            legacy_value=leg_wac,
                            projected_value=proj_wac,
                            message=f"WAC EUR mismatch for {pos_id}: {dec_leg_wac} != {dec_proj_wac}",
                            record_id=pos_id,
                        ))
                except (InvalidOperation, ValueError):
                    has_mismatch = True
                    summary.unresolved += 1

            # Compare WAC USD (R2: explicit presence and value equality)
            leg_wac_usd = leg_p.get("wac_usd")
            proj_wac_usd = proj_p.get("wac_usd")
            if (leg_wac_usd is None) != (proj_wac_usd is None):
                has_mismatch = True
                summary.financial_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{pos_id}].wac_usd",
                    discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                    legacy_value=leg_wac_usd,
                    projected_value=proj_wac_usd,
                    message=f"WAC USD presence mismatch for {pos_id}: legacy={leg_wac_usd} != projected={proj_wac_usd}",
                    record_id=pos_id,
                ))
            elif leg_wac_usd is not None:
                try:
                    dec_leg_usd = Decimal(str(leg_wac_usd))
                    dec_proj_usd = Decimal(str(proj_wac_usd))
                    if dec_leg_usd == dec_proj_usd:
                        raw_wac_usd = leg_p.get("raw_wac_usd", leg_wac_usd)
                        if raw_wac_usd == proj_wac_usd:
                            summary.exact_matches += 1
                        else:
                            summary.equivalent_values += 1
                    else:
                        has_mismatch = True
                        summary.financial_mismatches += 1
                        discrepancies.append(ParityDiscrepancy(
                            domain="portfolio_positions",
                            path=f"portfolio_snapshot[{pos_id}].wac_usd",
                            discrepancy_type=DiscrepancyType.FINANCIAL_MISMATCH.value,
                            legacy_value=leg_wac_usd,
                            projected_value=proj_wac_usd,
                            message=f"WAC USD mismatch for {pos_id}: {dec_leg_usd} != {dec_proj_usd}",
                            record_id=pos_id,
                        ))
                except (InvalidOperation, ValueError):
                    has_mismatch = True
                    summary.unresolved += 1

            # Compare Account ID (R2: explicit comparison)
            raw_leg_acc = leg_p.get("account_id", _MISSING)
            leg_acc = "PRIMARY" if raw_leg_acc is _MISSING else str(raw_leg_acc).strip().upper()
            raw_proj_acc = proj_p.get("account_id", _MISSING)
            proj_acc = "PRIMARY" if raw_proj_acc is _MISSING else str(raw_proj_acc).strip().upper()
            if leg_acc != proj_acc:
                has_mismatch = True
                summary.identity_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{pos_id}].account_id",
                    discrepancy_type=DiscrepancyType.IDENTITY_MISMATCH.value,
                    legacy_value=leg_p.get("account_id"),
                    projected_value=proj_p.get("account_id"),
                    message=f"Account ID mismatch for position {pos_id}: legacy={leg_acc} != projected={proj_acc}",
                    record_id=pos_id,
                ))

            # Compare Timestamp (R1)
            raw_pos_ts = leg_p.get("last_updated_at_utc", _MISSING)
            if raw_pos_ts is _MISSING:
                raw_pos_ts = leg_p.get("timestamp_utc", _MISSING)
            self._verify_timestamp_field(
                raw_pos_ts,
                proj_p.get("last_updated_at_utc"),
                "last_updated_at_utc",
                f"portfolio_snapshot[{pos_id}].last_updated_at_utc",
                "portfolio_positions",
                pos_id,
                summary,
                discrepancies,
                nullable=False,
                default_timestamp=default_timestamp,
                allow_date_only=allow_date_only,
                allow_naive_utc=allow_naive_utc,
            )

        # Check for unexpected projected positions (reverse check, R5)
        for (acc, ticker), proj_p in proj_map.items():
            if (acc, ticker) not in leg_map:
                pos_id = ticker if acc == "PRIMARY" else f"{acc}/{ticker}"
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="portfolio_positions",
                    path=f"portfolio_snapshot[{pos_id}]",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=proj_p,
                    message=f"Unexpected position for {pos_id} in projection",
                    record_id=pos_id,
                ))

    def _verify_watchlist_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
        default_timestamp: Optional[str] = None,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> None:
        """Compares watched tickers and structured watchlist records with unified normalization (R1, R2, R3, R5)."""
        try:
            consolidated_watchlist = consolidate_watchlist_representations(
                legacy,
                default_timestamp=default_timestamp,
                allow_date_only=allow_date_only,
                allow_naive_utc=allow_naive_utc,
                require_timestamp=False,
            )
        except (MigrationConflictError, MigrationValidationError) as exc:
            summary.unresolved += 1
            discrepancies.append(ParityDiscrepancy(
                domain="watchlist",
                path="watchlist",
                discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                legacy_value=None,
                projected_value=None,
                message=f"Conflicting watchlist representations in legacy source: {exc}",
            ))
            return

        leg_items: Dict[str, Dict[str, Any]] = {
            item["ticker"]: item for item in consolidated_watchlist
        }
        leg_set = set(leg_items.keys())
        proj_wl = projected.get("watched_tickers", [])
        proj_set = {str(t).strip().upper() for t in proj_wl if t}

        proj_items = {
            str(item.get("ticker")).strip().upper(): item
            for item in projected.get("watchlist", [])
            if isinstance(item, dict) and item.get("ticker")
        }

        # Compare ticker identities
        missing_tickers = leg_set - proj_set
        unexpected_tickers = proj_set - leg_set

        for missing in sorted(missing_tickers):
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

        for unexpected in sorted(unexpected_tickers):
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

        common_tickers = leg_set & proj_set
        for tk in sorted(common_tickers):
            summary.exact_matches += 1
            leg_item = leg_items[tk]
            proj_item = proj_items.get(tk)
            if proj_item is not None:
                # Status: explicit comparison with default ACTIVE
                raw_leg_status = leg_item.get("status", _MISSING)
                leg_status = "ACTIVE" if raw_leg_status is _MISSING else leg_item.get("status")
                raw_proj_status = proj_item.get("status", _MISSING)
                proj_status = "ACTIVE" if raw_proj_status is _MISSING else proj_item.get("status")
                if leg_status != proj_status:
                    summary.unresolved += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="watchlist",
                        path=f"watchlist[{tk}].status",
                        discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                        legacy_value=leg_item.get("status"),
                        projected_value=proj_item.get("status"),
                        message=f"Status mismatch for watchlist item {tk}: legacy={leg_status!r} != projected={proj_status!r}",
                        record_id=tk,
                    ))

                # Notes: explicit comparison handling None vs "" vs string
                leg_notes = leg_item.get("notes")
                proj_notes = proj_item.get("notes")
                if leg_notes != proj_notes:
                    summary.unresolved += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="watchlist",
                        path=f"watchlist[{tk}].notes",
                        discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                        legacy_value=leg_notes,
                        projected_value=proj_notes,
                        message=f"Notes mismatch for watchlist item {tk}: legacy={leg_notes!r} != projected={proj_notes!r}",
                        record_id=tk,
                    ))

                # Timestamp verification (R1)
                raw_wl_ts = leg_item.get("added_at_utc", _MISSING)
                if raw_wl_ts is _MISSING:
                    raw_wl_ts = leg_item.get("timestamp_utc", _MISSING)
                self._verify_timestamp_field(
                    raw_wl_ts,
                    proj_item.get("added_at_utc"),
                    "added_at_utc",
                    f"watchlist[{tk}].added_at_utc",
                    "watchlist",
                    tk,
                    summary,
                    discrepancies,
                    nullable=False,
                    default_timestamp=default_timestamp,
                    allow_date_only=allow_date_only,
                    allow_naive_utc=allow_naive_utc,
                )

        # Reverse check for structured watchlist items (R5)
        for tk, proj_item in proj_items.items():
            if tk not in leg_items and tk not in unexpected_tickers:
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="watchlist",
                    path=f"watchlist[{tk}]",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=proj_item,
                    message=f"Unexpected structured watchlist item {tk} in projection",
                    record_id=tk,
                ))

    def _verify_scout_categories_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
        default_timestamp: Optional[str] = None,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> None:
        """Compares scout categories and ticker mappings with unified normalization (R1, R2, R3, R5)."""
        try:
            consolidated_scout = consolidate_scout_representations(
                legacy,
                default_timestamp=default_timestamp,
                allow_date_only=allow_date_only,
                allow_naive_utc=allow_naive_utc,
                require_timestamp=False,
            )
        except (MigrationConflictError, MigrationValidationError) as exc:
            summary.unresolved += 1
            discrepancies.append(ParityDiscrepancy(
                domain="scout_categories",
                path="scout_categories",
                discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                legacy_value=None,
                projected_value=None,
                message=f"Conflicting scout representations in legacy source: {exc}",
            ))
            return

        leg_categories: Dict[str, Dict[str, Any]] = {
            c["category_name"]: c for c in consolidated_scout
        }

        proj_records = projected.get("scout_category_records", [])
        proj_map = {
            r.get("category_name"): {str(t).strip().upper() for t in r.get("tickers", []) if t}
            for r in proj_records
            if isinstance(r, dict) and r.get("category_name")
        }
        proj_cat_objs = {
            r.get("category_name"): r
            for r in proj_records
            if isinstance(r, dict) and r.get("category_name")
        }

        for cat_name, leg_info in leg_categories.items():
            if cat_name not in proj_map:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="scout_categories",
                    path=f"scout_categories[{cat_name}]",
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=sorted(list(leg_info.get("tickers", []))),
                    projected_value=None,
                    message=f"Scout category '{cat_name}' missing in projection",
                    record_id=cat_name,
                ))
            else:
                proj_tickers = proj_map[cat_name]
                leg_tickers = set(leg_info.get("tickers", []))
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

                proj_sc = proj_cat_objs[cat_name]
                leg_desc = leg_info.get("description")
                proj_desc = proj_sc.get("description")
                if leg_desc != proj_desc:
                    summary.unresolved += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="scout_categories",
                        path=f"scout_categories[{cat_name}].description",
                        discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                        legacy_value=leg_desc,
                        projected_value=proj_desc,
                        message=f"Description mismatch for scout category '{cat_name}': legacy={leg_desc!r} != projected={proj_desc!r}",
                        record_id=cat_name,
                    ))

                # Timestamp verification (R1)
                raw_sc_ts = leg_info.get("updated_at_utc", _MISSING)
                if raw_sc_ts is _MISSING:
                    raw_sc_ts = leg_info.get("timestamp_utc", _MISSING)
                self._verify_timestamp_field(
                    raw_sc_ts,
                    proj_sc.get("updated_at_utc"),
                    "updated_at_utc",
                    f"scout_categories[{cat_name}].updated_at_utc",
                    "scout_categories",
                    cat_name,
                    summary,
                    discrepancies,
                    nullable=False,
                    default_timestamp=default_timestamp,
                    allow_date_only=allow_date_only,
                    allow_naive_utc=allow_naive_utc,
                )

        # Reverse check for scout categories (R5)
        for cat_name, proj_tickers in proj_map.items():
            if cat_name not in leg_categories:
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="scout_categories",
                    path=f"scout_categories[{cat_name}]",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=sorted(list(proj_tickers)),
                    message=f"Unexpected scout category '{cat_name}' present in projection",
                    record_id=cat_name,
                ))

    def _verify_trade_lessons_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
        default_timestamp: Optional[str] = None,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> None:
        """Compares trade lessons across rule codes, directives, categories, and timestamps (R1, R2, R5)."""
        leg_lessons = legacy.get("trade_lessons", [])
        proj_lessons = projected.get("trade_lessons", [])
        proj_map = {l.get("rule_code"): l for l in proj_lessons if isinstance(l, dict) and l.get("rule_code")}
        leg_rule_codes: Set[str] = set()

        for item in leg_lessons:
            if not isinstance(item, dict):
                continue
            rule_code = item.get("rule_code")
            leg_directive = item.get("directive_text")
            if not rule_code and "rule" in item:
                rule_code, extracted_dir = extract_rule_code_and_directive(str(item["rule"]))
                if leg_directive is None:
                    leg_directive = extracted_dir or str(item["rule"])

            if rule_code is None:
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[id={item.get('id')}]",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=str(item.get("rule", ""))[:40],
                    projected_value=None,
                    message="Legacy trade lesson has uncodified format without 'L-xxx' prefix; not importable.",
                    record_id=str(item.get("id")),
                ))
                continue

            norm_code = str(rule_code).strip().upper()
            leg_rule_codes.add(norm_code)

            if norm_code not in proj_map:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{norm_code}]",
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=norm_code,
                    projected_value=None,
                    message=f"Trade lesson {norm_code} present in legacy source is missing in projection",
                    record_id=norm_code,
                ))
                continue

            proj_l = proj_map[norm_code]
            has_mismatch = False

            # Verify directive text (R2: explicit comparison)
            proj_directive = proj_l.get("directive_text")
            if leg_directive != proj_directive:
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{norm_code}].directive_text",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=leg_directive,
                    projected_value=proj_directive,
                    message=f"Directive text mismatch for trade lesson {norm_code}: legacy={leg_directive!r} != projected={proj_directive!r}",
                    record_id=norm_code,
                ))
                has_mismatch = True

            # Verify category (R2: explicit comparison with default MANDATE)
            raw_leg_cat = item.get("category", _MISSING)
            leg_cat = "MANDATE" if raw_leg_cat is _MISSING else item.get("category")
            raw_proj_cat = proj_l.get("category", _MISSING)
            proj_cat = "MANDATE" if raw_proj_cat is _MISSING else proj_l.get("category")
            if leg_cat != proj_cat:
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{norm_code}].category",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=item.get("category"),
                    projected_value=proj_l.get("category"),
                    message=f"Category mismatch for trade lesson {norm_code}: legacy={leg_cat!r} != projected={proj_cat!r}",
                    record_id=norm_code,
                ))
                has_mismatch = True

            # Verify status (R2: explicit comparison with default ACTIVE)
            raw_leg_status = item.get("status", _MISSING)
            leg_status = "ACTIVE" if raw_leg_status is _MISSING else item.get("status")
            raw_proj_status = proj_l.get("status", _MISSING)
            proj_status = "ACTIVE" if raw_proj_status is _MISSING else proj_l.get("status")
            if leg_status != proj_status:
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{norm_code}].status",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=item.get("status"),
                    projected_value=proj_l.get("status"),
                    message=f"Status mismatch for trade lesson {norm_code}: legacy={leg_status!r} != projected={proj_status!r}",
                    record_id=norm_code,
                ))
                has_mismatch = True

            # Verify created_at_utc (R1)
            raw_created = item["created_at_utc"] if "created_at_utc" in item else _MISSING
            self._verify_timestamp_field(
                raw_created,
                proj_l.get("created_at_utc"),
                "created_at_utc",
                f"trade_lessons[{norm_code}].created_at_utc",
                "trade_lessons",
                norm_code,
                summary,
                discrepancies,
                nullable=False,
                default_timestamp=default_timestamp,
                allow_date_only=allow_date_only,
                allow_naive_utc=allow_naive_utc,
            )

            # Verify last_reviewed_at_utc (R1, R2) - intentionally nullable
            raw_reviewed = item["last_reviewed_at_utc"] if "last_reviewed_at_utc" in item else _MISSING
            self._verify_timestamp_field(
                raw_reviewed,
                proj_l.get("last_reviewed_at_utc"),
                "last_reviewed_at_utc",
                f"trade_lessons[{norm_code}].last_reviewed_at_utc",
                "trade_lessons",
                norm_code,
                summary,
                discrepancies,
                nullable=True,
                default_timestamp=None,
                allow_date_only=allow_date_only,
                allow_naive_utc=allow_naive_utc,
            )

            if not has_mismatch:
                summary.exact_matches += 1

        # Reverse check for trade lessons (R5)
        for rule_code, proj_l in proj_map.items():
            norm_code = str(rule_code).strip().upper()
            if norm_code not in leg_rule_codes:
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="trade_lessons",
                    path=f"trade_lessons[{norm_code}]",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=proj_l,
                    message=f"Unexpected trade lesson {norm_code} in projection",
                    record_id=norm_code,
                ))

    def _verify_decision_log_domain(
        self,
        legacy: Dict[str, Any],
        projected: Dict[str, Any],
        summary: ParitySummary,
        discrepancies: List[ParityDiscrepancy],
        default_timestamp: Optional[str] = None,
        allow_date_only: bool = False,
        allow_naive_utc: bool = False,
    ) -> None:
        """Compares decision log entries across all 7 contract fields with strict collision-proof reverse checks (R1, R3, R5)."""
        leg_decisions = legacy.get("decision_log", [])
        proj_decisions = projected.get("decision_log", [])
        proj_by_session: Dict[str, List[Dict[str, Any]]] = {}
        for d in proj_decisions:
            if isinstance(d, dict) and d.get("session_id"):
                proj_by_session.setdefault(str(d["session_id"]).strip(), []).append(d)

        matched_projected_ids: Set[int] = set()

        def _verify_decision_record_fields(
            exp: Dict[str, Any],
            matching: Dict[str, Any],
            path_prefix: str,
        ) -> bool:
            has_diff = False
            sid = exp["session_id"]

            # 1. Action proposed
            if exp["action_proposed"] != matching.get("action_proposed"):
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="decision_log",
                    path=f"{path_prefix}.action_proposed",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=exp["action_proposed"],
                    projected_value=matching.get("action_proposed"),
                    message=f"Action mismatch for {path_prefix}",
                    record_id=sid,
                ))
                has_diff = True

            # 2. Decision outcome
            if exp["decision_outcome"] != matching.get("decision_outcome"):
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="decision_log",
                    path=f"{path_prefix}.decision_outcome",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=exp["decision_outcome"],
                    projected_value=matching.get("decision_outcome"),
                    message=f"Outcome mismatch for {path_prefix}",
                    record_id=sid,
                ))
                has_diff = True

            # 3. Ticker
            if exp.get("ticker") != matching.get("ticker"):
                summary.identity_mismatches += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="decision_log",
                    path=f"{path_prefix}.ticker",
                    discrepancy_type=DiscrepancyType.IDENTITY_MISMATCH.value,
                    legacy_value=exp.get("ticker"),
                    projected_value=matching.get("ticker"),
                    message=f"Ticker mismatch for {path_prefix}",
                    record_id=sid,
                ))
                has_diff = True

            # 4. Votes dict
            raw_leg_votes = exp.get("votes", _MISSING)
            leg_votes = {} if raw_leg_votes is _MISSING else exp.get("votes")
            raw_proj_votes = matching.get("votes", _MISSING)
            proj_votes = {} if raw_proj_votes is _MISSING else matching.get("votes")
            if not deep_equal(leg_votes, proj_votes):
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="decision_log",
                    path=f"{path_prefix}.votes",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=exp.get("votes"),
                    projected_value=matching.get("votes"),
                    message=f"Votes mismatch for {path_prefix}: legacy={leg_votes!r} != projected={proj_votes!r}",
                    record_id=sid,
                ))
                has_diff = True

            # 5. Transcript text
            if exp.get("transcript_text") != matching.get("transcript_text"):
                summary.unresolved += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="decision_log",
                    path=f"{path_prefix}.transcript_text",
                    discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                    legacy_value=exp.get("transcript_text"),
                    projected_value=matching.get("transcript_text"),
                    message=f"Transcript text mismatch for {path_prefix}",
                    record_id=sid,
                ))
                has_diff = True

            # 6. Timestamp UTC (R1)
            raw_decision_ts = exp["timestamp_utc"] if "timestamp_utc" in exp else _MISSING
            self._verify_timestamp_field(
                raw_decision_ts,
                matching.get("timestamp_utc"),
                "timestamp_utc",
                f"{path_prefix}.timestamp_utc",
                "decision_log",
                sid,
                summary,
                discrepancies,
                nullable=False,
                default_timestamp=default_timestamp,
                allow_date_only=allow_date_only,
                allow_naive_utc=allow_naive_utc,
            )

            return not has_diff

        def _match_and_verify(exp_rec: Dict[str, Any], path_desc: str) -> None:
            sid = exp_rec["session_id"]
            action = exp_rec["action_proposed"]
            ticker = exp_rec["ticker"]
            candidates = proj_by_session.get(sid, [])

            matching = None
            # Find an unmatched candidate
            for c in candidates:
                if id(c) in matched_projected_ids:
                    continue
                if c.get("action_proposed") == action and c.get("ticker") == ticker:
                    matching = c
                    break

            if not matching and ticker:
                for c in candidates:
                    if id(c) in matched_projected_ids:
                        continue
                    if c.get("ticker") == ticker:
                        matching = c
                        break

            if not matching:
                summary.missing_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="decision_log",
                    path=path_desc,
                    discrepancy_type=DiscrepancyType.MISSING_RECORD.value,
                    legacy_value=exp_rec,
                    projected_value=None,
                    message=f"Decision log entry {sid}/{action}/{ticker} missing in projection",
                    record_id=sid,
                ))
            else:
                matched_projected_ids.add(id(matching))
                if _verify_decision_record_fields(exp_rec, matching, path_desc):
                    summary.exact_matches += 1

        for idx, d in enumerate(leg_decisions):
            if not isinstance(d, dict):
                continue

            # Case A: Canonical structured decision record
            if "session_id" in d and "action_proposed" in d and "decision_outcome" in d:
                session_id = str(d["session_id"]).strip()
                action = str(d["action_proposed"]).strip()
                outcome = str(d["decision_outcome"]).strip()
                ticker = str(d.get("ticker")).strip().upper() if d.get("ticker") else None
                raw_votes = d.get("votes", _MISSING)
                votes_val = {} if raw_votes is _MISSING else d.get("votes")
                exp_rec = {
                    "session_id": session_id,
                    "action_proposed": action,
                    "decision_outcome": outcome,
                    "ticker": ticker,
                    "votes": votes_val,
                    "transcript_text": d.get("transcript_text"),
                    "timestamp_utc": d.get("timestamp_utc", _MISSING),
                }
                _match_and_verify(exp_rec, f"decision_log[{session_id}/{action}/{ticker}]")

            # Case B: Date-based legacy record (D-06, D1.3)
            elif "date" in d and ("lesson" in d or "action" in d or "outcome" in d):
                try:
                    trans_b = transform_legacy_decision_case_b(
                        d,
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                    )
                except Exception as exc:
                    summary.unresolved += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="decision_log",
                        path=f"decision_log[date={d.get('date')}]",
                        discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                        legacy_value=d,
                        projected_value=None,
                        message=f"Case B legacy transformation failed: {exc}",
                        record_id=str(d.get("date")),
                    ))
                    continue

                _match_and_verify(trans_b, f"decision_log[{trans_b['session_id']}]")

            # Case C: Nested council debate record (D-06, D1.3, R3)
            elif "decisions" in d and isinstance(d["decisions"], list):
                try:
                    trans_c_list = transform_legacy_decision_case_c(
                        d,
                        default_timestamp=default_timestamp,
                        allow_naive_utc=allow_naive_utc,
                    )
                except Exception as exc:
                    summary.unresolved += 1
                    discrepancies.append(ParityDiscrepancy(
                        domain="decision_log",
                        path=f"decision_log[timestamp={d.get('timestamp')}]",
                        discrepancy_type=DiscrepancyType.UNRESOLVED.value,
                        legacy_value=d,
                        projected_value=None,
                        message=f"Case C legacy transformation failed: {exc}",
                        record_id=str(d.get("timestamp")),
                    ))
                    continue

                for sub_exp in trans_c_list:
                    sid = sub_exp["session_id"]
                    ticker = sub_exp["ticker"]
                    _match_and_verify(sub_exp, f"decision_log[{sid}/{ticker}]")

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

        # Reverse check for unexpected projected decisions (R5)
        # Every projected decision must be mapped to a distinct legacy record (prevent collision evasion)
        for proj_d in proj_decisions:
            if not isinstance(proj_d, dict):
                continue
            if id(proj_d) not in matched_projected_ids:
                p_sid = str(proj_d.get("session_id") or "").strip()
                p_action = str(proj_d.get("action_proposed") or "").strip()
                p_ticker = str(proj_d.get("ticker")).strip().upper() if proj_d.get("ticker") else None
                summary.unexpected_records += 1
                discrepancies.append(ParityDiscrepancy(
                    domain="decision_log",
                    path=f"decision_log[{p_sid}/{p_action}/{p_ticker}]",
                    discrepancy_type=DiscrepancyType.UNEXPECTED_RECORD.value,
                    legacy_value=None,
                    projected_value=proj_d,
                    message=f"Unexpected decision record {p_sid}/{p_action}/{p_ticker} present in projection",
                    record_id=p_sid,
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
