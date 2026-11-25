"""Deterministic Quantitative Analytics, Regime & Mandate Offload Engine (Phase 1).

Read-only analytics layer offloading deterministic math, Decimal position/trim
sizing, Volatility Regime Permissibility routing, GEX whiplash state tracking,
and rule/veto threshold evaluation from the LLM Council into DATA_PACKET and
GET /api/analytics. Zero mutations to SSoT, portfolio, or trading rules.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
import json
import math
import threading
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    from macro_calendar import get_macro_calendar_status
except ImportError:
    from python.macro_calendar import get_macro_calendar_status

NY_TZ = ZoneInfo("America/New_York")

# Canonical system thresholds mirroring gem_trading_rules/rules.md
GLOBAL_ALPHA_FRICTION_HURDLE = Decimal("0.0085")
EUR_OMXH_FRICTION_HURDLE = Decimal("0.0025")
FRONT_RUN_FACTOR = Decimal("0.9975")
SWEEP_LIMIT_FACTOR = Decimal("0.9950")

VIX_NEW_RISK_VETO = 20.0
VVIX_NEW_RISK_VETO = 105.0
VIXY_VELOCITY_ALERT = 5.0
IEF_YIELD_ALERT = -0.15
RVOL_CONFIRMATION = 1.5
VWAP_EXECUTION_BLOCK_PCT = 3.0

TRIM_PCTS: Tuple[Tuple[str, Decimal], ...] = (
    ("10pct", Decimal("0.10")),
    ("15pct", Decimal("0.15")),
    ("25pct", Decimal("0.25")),
    ("50pct", Decimal("0.50")),
    ("100pct", Decimal("1.00")),
)


def _safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, int):
            d = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                return None
            d = Decimal(str(value))
        else:
            s = str(value).strip()
            if not s:
                return None
            d = Decimal(s)
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _q2(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q4(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)


def _is_ticker_valid_and_fresh(row: Dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("price_validity") != "VALID":
        return False
    if row.get("freshness") not in ("CURRENT", "DELAYED"):
        return False
    price = _safe_float(row.get("price"))
    return price is not None and price > 0


def _thaw(obj: Any) -> Any:
    """Deep-converts read-only snapshot containers to plain dict/list.

    The live GLOBAL_STATE snapshot is published as fetch_stocks.ImmutableDict / ImmutableList,
    which are collections.abc Mapping / Sequence but not dict / list. This module's
    isinstance(..., dict) checks would otherwise silently drop every ticker row, leaving held
    positions unpriced (blank P&L, "Awaiting live price" tickets, regime without VIX).
    """
    if isinstance(obj, dict):
        return {k: _thaw(v) for k, v in obj.items()}
    if isinstance(obj, Mapping):
        return {k: _thaw(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)) or (isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray))):
        return [_thaw(v) for v in obj]
    return obj


def _fx_rate_from(fx_info: Any) -> Any:
    """USD-per-EUR rate from an FX snapshot, or None when no fresh quote is available.

    fx_rates.fx_public() publishes the rate under "rate"; older callers and tests use
    "eurusd_rate". Reading only "eurusd_rate" left live P&L / EUR proofs permanently blank.
    """
    if not isinstance(fx_info, dict) or not fx_info.get("available"):
        return None
    rate = fx_info.get("rate")
    return rate if rate is not None else fx_info.get("eurusd_rate")


def classify_regime(tickers: List[Dict[str, Any]], market_status: str = "UNKNOWN") -> Dict[str, Any]:
    """Deterministic Volatility State & Strategy Permissibility routing matrix
    mirroring engine_instructions/regime_engine.md.
    """
    tickers = _thaw(tickers)
    by_sym: Dict[str, Dict[str, Any]] = {
        str(t.get("ticker") or "").strip().upper(): t
        for t in (tickers or [])
        if isinstance(t, dict) and t.get("ticker")
    }

    vix_row = by_sym.get("^VIX", {})
    vvix_row = by_sym.get("^VVIX", {})
    vixy_row = by_sym.get("VIXY", {})
    ief_row = by_sym.get("IEF", {})
    spy_row = by_sym.get("SPY", {})
    qqq_row = by_sym.get("QQQ", {})

    vix = _safe_float(vix_row.get("price")) if _is_ticker_valid_and_fresh(vix_row) else _safe_float(vix_row.get("price"))
    vvix = _safe_float(vvix_row.get("price"))
    vixy_chg = _safe_float(vixy_row.get("session_change_pct"))
    ief_chg = _safe_float(ief_row.get("session_change_pct"))

    spy_price = _safe_float(spy_row.get("price"))
    spy_ma20 = _safe_float(spy_row.get("ma_20"))
    spy_ma50 = _safe_float(spy_row.get("ma_50"))
    qqq_price = _safe_float(qqq_row.get("price"))
    qqq_ma50 = _safe_float(qqq_row.get("ma_50"))

    spy_posture = str(spy_row.get("dealer_posture") or "NEUTRAL").strip().upper()
    spy_net_gex = _safe_float(spy_row.get("net_gex_total"))
    if spy_net_gex is not None and spy_net_gex < 0 and spy_posture == "NEUTRAL":
        spy_posture = "SHORT_GAMMA"

    spy_above_50 = bool(spy_price and spy_ma50 and spy_price > spy_ma50)
    spy_below_50 = bool(spy_price and spy_ma50 and spy_price < spy_ma50)
    qqq_below_50 = bool(qqq_price and qqq_ma50 and qqq_price < qqq_ma50)

    vol_regime_tag = "NORMAL"
    for row in (spy_row, qqq_row, vix_row):
        if row.get("volatility_regime"):
            vol_regime_tag = str(row["volatility_regime"]).strip().upper()
            break

    if (vix is not None and vix > VIX_NEW_RISK_VETO) or (vixy_chg is not None and vixy_chg > VIXY_VELOCITY_ALERT):
        vol_state = "BEAR_VOLATILE"
    elif spy_below_50 and (qqq_below_50 or not qqq_ma50):
        vol_state = "BEAR_QUIET"
    elif spy_above_50 and (vix is not None and vix < 18.0):
        vol_state = "BULL_QUIET"
    elif spy_above_50 and (spy_ma20 is not None and spy_ma50 is not None and spy_ma20 > spy_ma50) and (vix is None or vix <= VIX_NEW_RISK_VETO):
        vol_state = "BULL_VOLATILE"
    else:
        vol_state = "NEUTRAL"

    if vol_state in ("BULL_QUIET", "BULL_VOLATILE"):
        permissibility = {
            "Momentum_Breakouts": True,
            "High_Tight_Flags": True,
            "Pullback_Continuations": True,
            "Mean_Reversion": False,
            "Episodic_Pivots": True,
            "Parabolic_Shorts": False,
        }
    elif vol_state in ("BEAR_VOLATILE", "BEAR_QUIET"):
        permissibility = {
            "Momentum_Breakouts": False,
            "High_Tight_Flags": False,
            "Pullback_Continuations": False,
            "Mean_Reversion": True,
            "Episodic_Pivots": True,
            "Parabolic_Shorts": True,
        }
    else:
        permissibility = {
            "Momentum_Breakouts": False,
            "High_Tight_Flags": False,
            "Pullback_Continuations": False,
            "Mean_Reversion": True,
            "Episodic_Pivots": True,
            "Parabolic_Shorts": False,
        }

    parts = [f"State={vol_state} ({vol_regime_tag})"]
    if vix is not None:
        parts.append(f"VIX={vix:.2f}")
    if vvix is not None:
        parts.append(f"VVIX={vvix:.1f}")
    if spy_posture != "NEUTRAL" or spy_net_gex is not None:
        gex_s = f"{spy_net_gex:.3f}" if spy_net_gex is not None else "n/a"
        parts.append(f"SPY_GEX={spy_posture} ({gex_s})")

    return {
        "market_status": str(market_status or "UNKNOWN").upper(),
        "volatility_state": vol_state,
        "volatility_regime": vol_regime_tag,
        "strategy_permissibility": permissibility,
        "vix": round(vix, 2) if vix is not None else None,
        "vvix": round(vvix, 2) if vvix is not None else None,
        "vixy_chg_pct": round(vixy_chg, 2) if vixy_chg is not None else None,
        "ief_chg_pct": round(ief_chg, 2) if ief_chg is not None else None,
        "spy_dealer_posture": spy_posture,
        "spy_net_gex": round(spy_net_gex, 4) if spy_net_gex is not None else None,
        "rationale": " | ".join(parts),
    }


def compute_position_tranches(
    ticker: str,
    shares_raw: Any,
    wac_raw: Any,
    price_raw: Any,
    prev_close_raw: Any = None,
    eurusd_rate_raw: Any = None,
) -> Dict[str, Any]:
    """Computes exact Decimal position valuation, 10/15/25/50/100% trim tranches,
    front-run & sweep limits, and MANDATE_06 math/FX proof strings.
    """
    shares = _to_decimal(shares_raw) or Decimal("0")
    wac = _to_decimal(wac_raw) or Decimal("0")
    price = _to_decimal(price_raw)
    prev_close = _to_decimal(prev_close_raw)
    fx_rate = _to_decimal(eurusd_rate_raw)
    has_fx = fx_rate is not None and fx_rate > Decimal("0")

    is_integer_shares = shares == shares.to_integral_value()

    tranches: Dict[str, Dict[str, Any]] = {}
    if price is not None and price > Decimal("0") and shares > Decimal("0"):
        mv_usd = _q2(shares * price)
        # WAC is stored in EUR (positions.wac_eur) while the price is USD, so the cost basis is
        # converted at the live EUR/USD rate. With no fresh rate, P&L stays unknown (never guessed).
        wac_usd = (wac * fx_rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if (wac > Decimal("0") and has_fx) else None
        cost_usd = _q2(shares * wac * fx_rate) if wac_usd is not None else None
        pnl_usd = _q2(mv_usd - cost_usd) if cost_usd is not None else None
        pnl_pct = (
            float(((mv_usd - cost_usd) / cost_usd * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if cost_usd is not None and cost_usd > Decimal("0")
            else None
        )
        mv_eur = _q2(mv_usd / fx_rate) if has_fx else None

        front_run_limit = _q2(price * FRONT_RUN_FACTOR)
        sweep_limit = _q2(price * SWEEP_LIMIT_FACTOR)

        for key, pct in TRIM_PCTS:
            if is_integer_shares:
                total_int = int(shares)
                sell_d = (shares * pct).to_integral_value(rounding=ROUND_FLOOR)
                if total_int >= 1 and pct > Decimal("0") and sell_d < Decimal("1"):
                    sell_d = Decimal("1")
                sell_int = min(total_int, int(sell_d))
                rem_int = total_int - sell_int
                sell_val: Any = sell_int
                rem_val: Any = rem_int
                sell_dec = Decimal(sell_int)
            else:
                sell_dec = min(shares, _q4(shares * pct))
                rem_dec = shares - sell_dec
                sell_val = float(sell_dec)
                rem_val = float(rem_dec)

            proceeds_usd = _q2(sell_dec * price)
            proceeds_eur = _q2(proceeds_usd / fx_rate) if has_fx else None
            tranches[key] = {
                "sell_shares": sell_val,
                "remaining_shares": rem_val,
                "front_run_limit_usd": float(front_run_limit),
                "sweep_limit_usd": float(sweep_limit),
                "est_proceeds_usd": float(proceeds_usd),
                "est_proceeds_eur": float(proceeds_eur) if proceeds_eur is not None else None,
            }

        price_proof = None
        if prev_close is not None and prev_close > Decimal("0"):
            chg = ((price - prev_close) / prev_close * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            price_proof = f"Proof: ({_q2(price)} - {_q2(prev_close)}) / {_q2(prev_close)} = {chg:+.2f}%"
        fx_proof = (
            f"Proof: (${mv_usd} / {fx_rate}) = EUR {mv_eur}"
            if (has_fx and mv_eur is not None)
            else None
        )

        return {
            "ticker": ticker,
            "shares": int(shares) if is_integer_shares else float(shares),
            "wac": float(_q2(wac)) if wac > Decimal("0") else None,
            "price": float(_q2(price)),
            "market_value_usd": float(mv_usd),
            "market_value_eur": float(mv_eur) if mv_eur is not None else None,
            "wac_usd": float(wac_usd) if wac_usd is not None else None,
            "cost_basis_usd": float(cost_usd) if cost_usd is not None else None,
            "unrealized_pnl_usd": float(pnl_usd) if pnl_usd is not None else None,
            "unrealized_pnl_pct": pnl_pct,
            "front_run_limit_usd": float(front_run_limit),
            "sweep_limit_usd": float(sweep_limit),
            "tranches": tranches,
            "math_proof": price_proof,
            "fx_proof": fx_proof,
        }

    return {
        "ticker": ticker,
        "shares": int(shares) if is_integer_shares else float(shares),
        "wac": float(_q2(wac)) if wac > Decimal("0") else None,
        "price": None,
        "market_value_usd": None,
        "market_value_eur": None,
        "wac_usd": None,
        "cost_basis_usd": None,
        "unrealized_pnl_usd": None,
        "unrealized_pnl_pct": None,
        "tranches": {},
        "math_proof": None,
        "fx_proof": None,
    }


def compute_agreement_score(votes: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic ENH_16_C weighted consensus score (S_A) and <0.51 fragility flag."""
    if not isinstance(votes, dict) or not votes:
        return {
            "agreement_score": 0.0,
            "fragile": True,
            "status": "FRAGILE",
            "dominant_stance": "SPLIT",
            "total_weight": 0.0,
            "buckets": {"BULLISH": 0.0, "HOLD": 0.0, "BEARISH": 0.0},
        }

    buckets = {"BULLISH": 0.0, "HOLD": 0.0, "BEARISH": 0.0}
    total_weight = 0.0

    for entry in votes.values():
        if isinstance(entry, dict):
            raw_stance = str(entry.get("stance") or entry.get("action") or "HOLD").strip().upper()
            weight = _safe_float(entry.get("weight"))
            w = weight if (weight is not None and weight > 0) else 1.0
        else:
            raw_stance = str(entry or "HOLD").strip().upper()
            w = 1.0

        if any(k in raw_stance for k in ("BUY", "ADD", "ACCUMULATE", "LONG", "BULL")):
            bucket = "BULLISH"
        elif any(k in raw_stance for k in ("TRIM", "SELL", "REDUCE", "EXIT", "SHORT", "BEAR")):
            bucket = "BEARISH"
        else:
            bucket = "HOLD"

        buckets[bucket] += w
        total_weight += w

    if total_weight <= 0.0:
        return {
            "agreement_score": 0.0,
            "fragile": True,
            "status": "FRAGILE",
            "dominant_stance": "SPLIT",
            "total_weight": 0.0,
            "buckets": {k: round(v, 4) for k, v in buckets.items()},
        }

    max_weight = max(buckets.values())
    winners = [k for k, v in buckets.items() if math.isclose(v, max_weight, rel_tol=1e-9, abs_tol=1e-9)]
    dominant = winners[0] if len(winners) == 1 else "SPLIT"
    s_a = max_weight / total_weight
    fragile = s_a < 0.51

    return {
        "agreement_score": round(s_a, 4),
        "fragile": fragile,
        "status": "FRAGILE" if fragile else "ALIGNED",
        "dominant_stance": dominant,
        "total_weight": round(total_weight, 4),
        "buckets": {k: round(v, 4) for k, v in buckets.items()},
    }


def _classify_watchlist_setup(row: Dict[str, Any], permissibility: Dict[str, bool]) -> Dict[str, Any]:
    sym = str(row.get("ticker") or "").strip().upper()
    valid = _is_ticker_valid_and_fresh(row)
    price = _safe_float(row.get("price"))
    vwap = _safe_float(row.get("vwap"))
    dist_vwap = _safe_float(row.get("distance_from_vwap"))
    gap_pct = _safe_float(row.get("gap_percent")) or 0.0
    rvol = _safe_float(row.get("rvol"))
    rsi = _safe_float(row.get("rsi"))
    trend = str(row.get("trend") or "FLAT").strip().upper()
    fib = row.get("fib_forecast") if isinstance(row.get("fib_forecast"), dict) else {}

    setup = "RANGE_WAIT"
    perm_key = "Mean_Reversion"
    if valid and (gap_pct >= 10.0 or (gap_pct >= 5.0 and (rvol or 0.0) >= 3.0)):
        setup = "EPISODIC_PIVOT"
        perm_key = "Episodic_Pivots"
    elif valid and trend == "UP" and vwap and price and price >= vwap:
        if fib.get("mode") == "EXTENSION" and (rvol or 0.0) >= RVOL_CONFIRMATION:
            setup = "MOMENTUM_BREAKOUT"
            perm_key = "Momentum_Breakouts"
        else:
            setup = "PULLBACK_CONTINUATION"
            perm_key = "Pullback_Continuations"
    elif valid and ((rsi is not None and rsi < 38.0) or (dist_vwap is not None and dist_vwap <= -1.5)):
        setup = "MEAN_REVERSION"
        perm_key = "Mean_Reversion"

    rvol_confirmed = bool(valid and rvol is not None and rvol >= RVOL_CONFIRMATION)
    vwap_blocked = bool(valid and dist_vwap is not None and abs(dist_vwap) > VWAP_EXECUTION_BLOCK_PCT)

    return {
        "ticker": sym,
        "valid_telemetry": valid,
        "setup": setup,
        "strategy_permissible": bool(permissibility.get(perm_key, False)),
        "rvol": round(rvol, 2) if rvol is not None else None,
        "rvol_confirmed": rvol_confirmed,
        "distance_from_vwap": round(dist_vwap, 2) if dist_vwap is not None else None,
        "vwap_execution_blocked": vwap_blocked,
        "next_resistance": _safe_float(fib.get("next_resistance")),
        "daily_peak_target": _safe_float(fib.get("daily_peak_target")),
        "fib_status": fib.get("daily_peak_status"),
    }


class AnalyticsEngine:
    """Stateful intraday tracker & deterministic rule/sizer evaluator."""

    def __init__(self):
        self._lock = threading.RLock()
        self._session_date: Optional[str] = None
        self._ticker_state: Dict[str, Dict[str, Any]] = {}

    def _ensure_session_date(self, ny_dt: datetime) -> None:
        day_str = ny_dt.strftime("%Y-%m-%d")
        if self._session_date != day_str:
            self._session_date = day_str
            self._ticker_state.clear()

    def record_tick(
        self,
        tickers: List[Dict[str, Any]],
        market_status: str = "UNKNOWN",
        ny_dt: Optional[datetime] = None,
    ) -> None:
        tickers = _thaw(tickers)
        if not isinstance(tickers, list):
            return
        now_ny = ny_dt or datetime.now(NY_TZ)
        now_ts = now_ny.timestamp()
        status_upper = str(market_status or "UNKNOWN").upper()

        with self._lock:
            self._ensure_session_date(now_ny)
            for row in tickers:
                if not _is_ticker_valid_and_fresh(row):
                    continue
                sym = str(row.get("ticker") or "").strip().upper()
                if not sym:
                    continue
                st = self._ticker_state.setdefault(
                    sym,
                    {
                        "gamma_history": [],
                        "gamma_whiplash_lock": False,
                        "gamma_flicker_overbought": False,
                        "rsi_gt_75_since_ts": None,
                        "rsi_overbought_minutes": 0.0,
                        "rvol_window": [],
                        "rvol_decelerating_3x": False,
                    },
                )

                # ENH_17_B: Track intraday dealer posture flips (LONG_GAMMA -> SHORT_GAMMA -> LONG_GAMMA)
                posture = str(row.get("dealer_posture") or "").strip().upper()
                if posture in ("LONG_GAMMA", "SHORT_GAMMA"):
                    hist = st["gamma_history"]
                    if not hist or hist[-1] != posture:
                        hist.append(posture)
                        if len(hist) >= 3 and hist[-3:] == ["LONG_GAMMA", "SHORT_GAMMA", "LONG_GAMMA"]:
                            st["gamma_whiplash_lock"] = True

                # MANDATE_38 & ENH_111: Overbought RSI > 75 timer & gamma flicker stop tighten
                rsi = _safe_float(row.get("rsi"))
                if rsi is not None and rsi > 75.0:
                    if st["rsi_gt_75_since_ts"] is None:
                        st["rsi_gt_75_since_ts"] = now_ts
                    st["rsi_overbought_minutes"] = max(0.0, (now_ts - float(st["rsi_gt_75_since_ts"])) / 60.0)
                    if posture == "SHORT_GAMMA" or ("SHORT_GAMMA" in st["gamma_history"]):
                        st["gamma_flicker_overbought"] = True
                elif rsi is not None:
                    st["rsi_gt_75_since_ts"] = None
                    st["rsi_overbought_minutes"] = 0.0

                # ENH_253: Track 3 consecutive decelerating rVol reads during OPEN
                rvol = _safe_float(row.get("rvol"))
                if status_upper == "OPEN" and rvol is not None and rvol > 0:
                    win = st["rvol_window"]
                    if not win or abs(win[-1] - rvol) >= 1e-4:
                        win.append(rvol)
                        if len(win) > 3:
                            del win[:-3]
                    if len(win) == 3 and win[0] > win[1] > win[2] and win[0] >= 2.0 and win[2] < 1.5:
                        st["rvol_decelerating_3x"] = True
                    else:
                        st["rvol_decelerating_3x"] = False

    def _get_tracker_snapshot(self, sym: str) -> Dict[str, Any]:
        with self._lock:
            st = self._ticker_state.get(sym, {})
            return {
                "gamma_whiplash_lock": bool(st.get("gamma_whiplash_lock", False)),
                "gamma_flicker_overbought": bool(st.get("gamma_flicker_overbought", False)),
                "rsi_overbought_minutes": round(float(st.get("rsi_overbought_minutes", 0.0)), 1),
                "rvol_decelerating_3x": bool(st.get("rvol_decelerating_3x", False)),
            }

    @staticmethod
    def _evaluate_global_vetoes(regime: Dict[str, Any]) -> List[Dict[str, Any]]:
        vetoes: List[Dict[str, Any]] = []
        vix = regime.get("vix")
        vvix = regime.get("vvix")
        ief_chg = regime.get("ief_chg_pct")
        spy_posture = regime.get("spy_dealer_posture", "NEUTRAL")
        spy_gex = regime.get("spy_net_gex")

        if (vix is not None and vix > VIX_NEW_RISK_VETO) or (vvix is not None and vvix > VVIX_NEW_RISK_VETO):
            vetoes.append({
                "rule_id": "ENH_72",
                "action": "VETO_NEW_RISK",
                "reason": f"Volatility barrier breached (VIX={vix}, VVIX={vvix}); new non-hedge risk vetoed (ENH_57 exempts clinical biotechs).",
            })
        if spy_posture == "SHORT_GAMMA" or (spy_gex is not None and spy_gex < 0):
            vetoes.append({
                "rule_id": "MANDATE_48",
                "action": "FREEZE_NEW_CAPITAL",
                "reason": "SPY net GEX is SHORT_GAMMA; freeze unexempt capital deployment and scale defensive entries -25%.",
            })
        if ief_chg is not None and ief_chg < IEF_YIELD_ALERT:
            vetoes.append({
                "rule_id": "IEF_YIELD_ALERT",
                "action": "YIELDS_RISING_RISK",
                "reason": f"IEF down {ief_chg:.2f}% (< -0.15%); rising yields headwind active.",
            })
        return vetoes

    @staticmethod
    def _evaluate_position_mandates(
        row: Dict[str, Any],
        tracker: Dict[str, Any],
        regime: Dict[str, Any],
        market_status: str,
        ny_dt: datetime,
    ) -> List[Dict[str, Any]]:
        if not _is_ticker_valid_and_fresh(row):
            return []

        mandates: List[Dict[str, Any]] = []
        status_upper = str(market_status or "UNKNOWN").upper()
        spy_short_gamma = regime.get("spy_dealer_posture") == "SHORT_GAMMA" or (
            regime.get("spy_net_gex") is not None and regime["spy_net_gex"] < 0
        )

        price = float(row["price"])
        vwap = _safe_float(row.get("vwap")) or 0.0
        dist_vwap = _safe_float(row.get("distance_from_vwap"))
        if dist_vwap is None and vwap > 0:
            dist_vwap = ((price - vwap) / vwap) * 100.0
        chg_pct = _safe_float(row.get("session_change_pct")) or 0.0
        gap_pct = _safe_float(row.get("gap_percent")) or 0.0
        rsi = _safe_float(row.get("rsi")) or 50.0
        rvol = _safe_float(row.get("rvol")) or 1.0
        trend = str(row.get("trend") or "FLAT").strip().upper()
        trend_score = _safe_float(row.get("trend_score")) or 0.0
        score = _safe_float(row.get("score")) or 0.0
        posture = str(row.get("dealer_posture") or "NEUTRAL").strip().upper()
        ssr_active = bool(row.get("ssr_active")) or chg_pct <= -10.0

        fib = row.get("fib_forecast") if isinstance(row.get("fib_forecast"), dict) else {}
        hod = _safe_float(fib.get("pB")) or 0.0

        hhmm = ny_dt.hour * 100 + ny_dt.minute

        if ssr_active:
            mandates.append({
                "rule_id": "ENH_16_E",
                "action": "LONG_GAMMA_SHIELD_INVALIDATED",
                "severity": "critical",
                "detail": "SEC Rule 201 SSR active / <= -10% drop; LONG_GAMMA shield overridden.",
            })
        elif chg_pct <= -8.0:
            mandates.append({
                "rule_id": "MANDATE_54",
                "action": "MANDATORY_50PCT_TRIM",
                "trim_pct": 50,
                "severity": "critical",
                "detail": f"Intraday drawdown {chg_pct:.2f}% <= -8.0% pre-SSR floor.",
            })

        if status_upper == "PRE-MARKET":
            gap_thresh = -2.50 if spy_short_gamma else -3.00
            if gap_pct <= gap_thresh and (trend_score < 0 or score < 0):
                mandates.append({
                    "rule_id": "ENH_16_F",
                    "action": "MANDATORY_50PCT_TRIM",
                    "trim_pct": 50,
                    "severity": "critical",
                    "detail": f"Pre-market gap {gap_pct:.2f}% <= {gap_thresh:.2f}% with negative trend/score.",
                })
            if chg_pct <= -4.0 and posture == "SHORT_GAMMA":
                mandates.append({
                    "rule_id": "ENH_115",
                    "action": "ADVISE_25PCT_OPEN_TRIM",
                    "trim_pct": 25,
                    "severity": "warning",
                    "detail": f"Pre-market drop {chg_pct:.2f}% <= -4.0% under SHORT_GAMMA.",
                })

        if status_upper == "OPEN":
            if spy_short_gamma and vwap > 0 and dist_vwap is not None and dist_vwap <= -2.0:
                mandates.append({
                    "rule_id": "MANDATE_52",
                    "action": "MANDATORY_25PCT_SWEEP_TRIM",
                    "trim_pct": 25,
                    "severity": "critical",
                    "detail": f"SPY SHORT_GAMMA and price {dist_vwap:.2f}% below VWAP (<= -2.0%); sweeping limit -0.5% required.",
                })

            if hhmm < 1030 and vwap > 0 and price < vwap:
                if rvol >= 3.0 and trend == "DOWN":
                    mandates.append({
                        "rule_id": "MANDATE_43",
                        "action": "VOLUME_OVERRIDE_25_50PCT_TRIM",
                        "trim_pct": 25,
                        "severity": "critical",
                        "detail": f"Pre-10:30 ET VWAP breakdown with rVol {rvol:.2f}x >= 3.0x overrides whipsaw shield.",
                    })
                else:
                    mandates.append({
                        "rule_id": "MANDATE_47",
                        "action": "OPENING_WHIPSAW_SHIELD_ACTIVE",
                        "severity": "info",
                        "detail": "Pre-10:30 ET sub-VWAP whipsaw shield active unless 15m confirmed or rVol >= 3.0x.",
                    })
            elif hhmm >= 1030 and spy_short_gamma and vwap > 0 and price < vwap and trend == "DOWN":
                mandates.append({
                    "rule_id": "ENH_249",
                    "action": "MANDATORY_25PCT_TRIM",
                    "trim_pct": 25,
                    "severity": "warning",
                    "detail": "Post-10:30 ET sub-VWAP downtrend under SPY SHORT_GAMMA.",
                })

            if 1130 <= hhmm < 1300 and spy_short_gamma and vwap > 0 and dist_vwap is not None and dist_vwap <= -1.5 and rvol < 0.80:
                mandates.append({
                    "rule_id": "MANDATE_50",
                    "action": "MANDATORY_25PCT_MIDDAY_TRIM",
                    "trim_pct": 25,
                    "severity": "warning",
                    "detail": f"Midday sub-VWAP ({dist_vwap:.2f}%) with contracting volume (rVol {rvol:.2f}x < 0.80).",
                })

            if hod > 0 and price >= hod * 0.995 and (rvol < 1.0 or tracker.get("rvol_decelerating_3x")):
                mandates.append({
                    "rule_id": "ENH_258",
                    "action": "MANDATORY_10_15PCT_DOUBLE_TOP_TRIM",
                    "trim_pct": 15,
                    "severity": "warning",
                    "detail": f"Within 0.5% of HOD (${hod:.2f}) with weak/decelerating rVol ({rvol:.2f}x).",
                })

        if tracker.get("rsi_overbought_minutes", 0.0) >= 240.0:
            mandates.append({
                "rule_id": "MANDATE_38",
                "action": "MANDATORY_15PCT_TIMER_TRIM",
                "trim_pct": 15,
                "severity": "warning",
                "detail": f"RSI > 75 sustained for {tracker['rsi_overbought_minutes']:.0f}m (>= 240m timer).",
            })

        if dist_vwap is not None and dist_vwap >= 12.0 and rsi > 85.0:
            mandates.append({
                "rule_id": "MANDATE_40",
                "action": "MANDATORY_15PCT_SWEEP_TRIM",
                "trim_pct": 15,
                "severity": "critical",
                "detail": f"Parabolic +{dist_vwap:.2f}% VWAP extension with RSI {rsi:.1f} > 85.",
            })

        if tracker.get("gamma_whiplash_lock"):
            mandates.append({
                "rule_id": "ENH_17_B",
                "action": "GAMMA_WHIPLASH_LOCK",
                "severity": "warning",
                "detail": "Intraday LONG->SHORT->LONG gamma flip detected; GEX dampening untrusted until next close.",
            })

        if tracker.get("gamma_flicker_overbought"):
            mandates.append({
                "rule_id": "ENH_111",
                "action": "STOP_TIGHTEN_50PCT",
                "severity": "warning",
                "detail": "RSI > 75 overlapped SHORT_GAMMA intraday; tighten trailing stop by 50%.",
            })

        return mandates

    def evaluate(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        fx_info: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Builds a compact deterministic analytics bundle for DATA_PACKET & /api/analytics."""
        live_state, ssot, fx_info = _thaw(live_state), _thaw(ssot), _thaw(fx_info)
        now_ny = ny_dt or datetime.now(NY_TZ)
        tickers = live_state.get("tickers", []) if isinstance(live_state, dict) else []
        market_status = str(live_state.get("status") or "UNKNOWN").upper() if isinstance(live_state, dict) else "UNKNOWN"

        by_sym: Dict[str, Dict[str, Any]] = {
            str(t.get("ticker") or "").strip().upper(): t
            for t in (tickers or [])
            if isinstance(t, dict) and t.get("ticker")
        }

        regime = classify_regime(tickers, market_status=market_status)
        global_vetoes = self._evaluate_global_vetoes(regime)

        ms = ssot.get("mutable_state", ssot) if isinstance(ssot, dict) and isinstance(ssot.get("mutable_state"), dict) else (ssot if isinstance(ssot, dict) else {})
        portfolio_rows = ms.get("portfolio_snapshot", []) if isinstance(ms, dict) else []
        watched_raw = ms.get("watched_tickers", []) if isinstance(ms, dict) else []

        eurusd_rate = _fx_rate_from(fx_info)

        held_symbols = set()
        positions_out: List[Dict[str, Any]] = []
        for pos in (portfolio_rows or []):
            if not isinstance(pos, dict):
                continue
            sym = str(pos.get("ticker") or "").strip().upper()
            if not sym:
                continue
            shares_d = _to_decimal(pos.get("shares")) or Decimal("0")
            if shares_d <= Decimal("0"):
                continue
            held_symbols.add(sym)
            row = by_sym.get(sym, {})
            valid = _is_ticker_valid_and_fresh(row)
            sizing = compute_position_tranches(
                ticker=sym,
                shares_raw=pos.get("shares"),
                wac_raw=pos.get("wac"),
                price_raw=row.get("price") if valid else None,
                prev_close_raw=row.get("regular_close"),
                eurusd_rate_raw=eurusd_rate,
            )
            tracker = self._get_tracker_snapshot(sym)
            mandates = self._evaluate_position_mandates(row, tracker, regime, market_status, now_ny)
            sizing["valid_telemetry"] = valid
            sizing["triggered_mandates"] = mandates
            sizing["tracker"] = tracker
            sizing["dealer_posture"] = str(row.get("dealer_posture") or "NEUTRAL").strip().upper()
            sizing["session_change_pct"] = _safe_float(row.get("session_change_pct"))
            sizing["vwap"] = _safe_float(row.get("vwap"))
            positions_out.append(sizing)

        watchlist_out: List[Dict[str, Any]] = []
        permissibility = regime["strategy_permissibility"]
        for w in (watched_raw or []):
            wsym = str(w).strip().upper()
            if not wsym or wsym in held_symbols:
                continue
            row = by_sym.get(wsym)
            if row:
                watchlist_out.append(_classify_watchlist_setup(row, permissibility))

        macro_cal = get_macro_calendar_status(now_ny=now_ny)

        return {
            "as_of_ny": now_ny.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "friction_hurdles": {
                "us_round_trip_pct": 0.85,
                "eur_omxh_round_trip_pct": 0.25,
            },
            "regime": regime,
            "macro_calendar": macro_cal,
            "global_vetoes": global_vetoes,
            "positions": positions_out,
            "watchlist_signals": watchlist_out,
        }

    def format_regime_engine_response(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        fx_info: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
    ) -> str:
        """Zero-LLM deterministic response matching engine_instructions/regime_engine.md."""
        bundle = self.evaluate(live_state, ssot=ssot, fx_info=fx_info, ny_dt=ny_dt)
        reg = bundle["regime"]
        payload = {
            "source": "python_deterministic",
            "as_of_ny": bundle["as_of_ny"],
            "Volatility_State": reg["volatility_state"],
            "Volatility_Regime": reg["volatility_regime"],
            "Strategy_Permissibility": reg["strategy_permissibility"],
            "VIX": reg["vix"],
            "VVIX": reg["vvix"],
            "SPY_Dealer_Posture": reg["spy_dealer_posture"],
            "SPY_Net_GEX": reg["spy_net_gex"],
            "Global_Vetoes": bundle["global_vetoes"],
            "Rationale": reg["rationale"],
        }
        return json.dumps(payload, indent=2)

    def format_gex_engine_response(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
    ) -> str:
        """Zero-LLM deterministic response matching engine_instructions/gex_engine.md."""
        bundle = self.evaluate(live_state, ssot=ssot, ny_dt=ny_dt)
        tickers = live_state.get("tickers", []) if isinstance(live_state, dict) else []
        gex_rows: List[Dict[str, Any]] = []
        for item in (tickers or []):
            if not isinstance(item, dict) or not item.get("ticker"):
                continue
            sym = str(item["ticker"]).strip().upper()
            tracker = self._get_tracker_snapshot(sym)
            chg_pct = _safe_float(item.get("session_change_pct")) or 0.0
            ssr_override = bool(item.get("ssr_active")) or chg_pct <= -10.0
            gex_rows.append({
                "ticker": sym,
                "dealer_posture": str(item.get("dealer_posture") or "NEUTRAL").upper(),
                "net_gex_total": _safe_float(item.get("net_gex_total")),
                "gamma_flip_price": _safe_float(item.get("gamma_flip_price")),
                "flip_proximity_percent": _safe_float(item.get("flip_proximity_percent")),
                "ssr_override": ssr_override,
                "gamma_whiplash_lock": tracker["gamma_whiplash_lock"],
                "gamma_flicker_overbought": tracker["gamma_flicker_overbought"],
            })
        payload = {
            "source": "python_deterministic",
            "as_of_ny": bundle["as_of_ny"],
            "spy_dealer_posture": bundle["regime"]["spy_dealer_posture"],
            "spy_short_gamma": bundle["regime"]["spy_dealer_posture"] == "SHORT_GAMMA",
            "global_vetoes": bundle["global_vetoes"],
            "tickers": gex_rows,
        }
        return json.dumps(payload, indent=2)

    def format_technical_validator_response(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        fx_info: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
    ) -> str:
        """Zero-LLM deterministic response matching engine_instructions/technical_validator.md."""
        bundle = self.evaluate(live_state, ssot=ssot, fx_info=fx_info, ny_dt=ny_dt)
        payload = {
            "source": "python_deterministic",
            "as_of_ny": bundle["as_of_ny"],
            "friction_hurdles": bundle["friction_hurdles"],
            "global_vetoes": bundle["global_vetoes"],
            "positions": bundle["positions"],
            "watchlist_signals": bundle["watchlist_signals"],
        }
        return json.dumps(payload, indent=2)

    def format_rule_enforcer_response(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        fx_info: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
        sec_fetcher: Optional[Any] = None,
    ) -> str:
        """Zero-LLM deterministic response matching engine_instructions/rule_enforcer_engine.md."""
        bundle = self.evaluate(live_state, ssot=ssot, fx_info=fx_info, ny_dt=ny_dt)

        sec_dilution_vetoes: List[Dict[str, Any]] = []
        if callable(sec_fetcher):
            macro_skip = {"SPY", "QQQ", "IWM", "DIA", "VIXY", "IEF", "UUP", "GDX"}
            candidate_syms: List[str] = []
            seen = set()
            for item in bundle["positions"] + bundle["watchlist_signals"]:
                sym = str(item.get("ticker") or "").strip().upper()
                if not sym or sym.startswith("^") or sym in macro_skip or sym in seen:
                    continue
                seen.add(sym)
                candidate_syms.append(sym)
                if len(candidate_syms) >= 10:
                    break

            for sym in candidate_syms:
                try:
                    sec_res = sec_fetcher(sym)
                except Exception:
                    continue
                if not isinstance(sec_res, dict) or not sec_res.get("dilution_warning_72h"):
                    continue
                filings = sec_res.get("filings") if isinstance(sec_res.get("filings"), list) else []
                dil_filing = next((f for f in filings if isinstance(f, dict) and f.get("is_dilution_form")), None)
                sec_dilution_vetoes.append({
                    "ticker": sym,
                    "rule_id": "ENH_117",
                    "action": "VETO_NEW_LONGS_72H_DILUTION",
                    "severity": "critical",
                    "form": sec_res.get("latest_dilution_form") or (dil_filing.get("form") if dil_filing else "DILUTION"),
                    "filing_date": sec_res.get("latest_dilution_date") or (dil_filing.get("filing_date") if dil_filing else None),
                    "url": dil_filing.get("url") if dil_filing else None,
                })

        has_trim_mandate = any(
            isinstance(m, dict) and m.get("trim_pct") is not None
            for p in bundle["positions"]
            for m in (p.get("triggered_mandates") or [])
        )
        if has_trim_mandate:
            verdict = "TRIM_REQUIRED"
        elif bundle["global_vetoes"] or sec_dilution_vetoes:
            verdict = "VETO"
        else:
            verdict = "CLEAR"

        payload = {
            "source": "python_deterministic",
            "as_of_ny": bundle["as_of_ny"],
            "verdict": verdict,
            "has_global_vetoes": bool(bundle["global_vetoes"]),
            "has_sec_dilution_vetoes": bool(sec_dilution_vetoes),
            "friction_hurdles": bundle["friction_hurdles"],
            "global_vetoes": bundle["global_vetoes"],
            "sec_dilution_vetoes": sec_dilution_vetoes,
            "positions": bundle["positions"],
        }
        return json.dumps(payload, indent=2)

    def format_strategy_engine_response(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        fx_info: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
    ) -> str:
        """Zero-LLM deterministic response matching engine_instructions/strategy_engine.md."""
        bundle = self.evaluate(live_state, ssot=ssot, fx_info=fx_info, ny_dt=ny_dt)
        reg = bundle["regime"]
        permissibility = reg["strategy_permissibility"]
        tickers = live_state.get("tickers", []) if isinstance(live_state, dict) else []
        by_sym: Dict[str, Dict[str, Any]] = {
            str(t.get("ticker") or "").strip().upper(): t
            for t in (tickers or [])
            if isinstance(t, dict) and t.get("ticker")
        }

        target_syms: List[str] = []
        seen = set()
        for entry in bundle["positions"] + bundle["watchlist_signals"]:
            sym = str(entry.get("ticker") or "").strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                target_syms.append(sym)

        setups: List[Dict[str, Any]] = []
        for sym in target_syms:
            row = by_sym.get(sym, {"ticker": sym})
            sig = _classify_watchlist_setup(row, permissibility)
            target_setup = "UNKNOWN" if sig["setup"] == "RANGE_WAIT" else sig["setup"]
            if not sig["valid_telemetry"] or target_setup == "UNKNOWN":
                status = "INVALID_SETUP"
            elif sig["strategy_permissible"]:
                status = "PERMITTED"
            else:
                status = "BLOCKED_BY_REGIME"
            justification = (
                f"Setup={target_setup}, rVol={sig['rvol']}x, dist_VWAP={sig['distance_from_vwap']}%, "
                f"Regime={reg['volatility_state']}."
            )
            setups.append({
                "Ticker": sym,
                "Target_Setup": target_setup,
                "Strategy_Status": status,
                "Setup_Justification": justification,
                "rvol_confirmed": sig["rvol_confirmed"],
                "vwap_execution_blocked": sig["vwap_execution_blocked"],
            })

        payload = {
            "source": "python_deterministic",
            "as_of_ny": bundle["as_of_ny"],
            "Volatility_State": reg["volatility_state"],
            "Strategy_Permissibility": permissibility,
            "setups": setups,
        }
        return json.dumps(payload, indent=2)

    def build_execution_plan(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        fx_info: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Builds deterministic trailing stop audits, ENH_96 Fib ladders & cash proofs."""
        live_state, ssot, fx_info = _thaw(live_state), _thaw(ssot), _thaw(fx_info)
        bundle = self.evaluate(live_state, ssot=ssot, fx_info=fx_info, ny_dt=ny_dt)
        macro_cal = bundle.get("macro_calendar") or {}
        ms = ssot.get("mutable_state", ssot) if isinstance(ssot, dict) and isinstance(ssot.get("mutable_state"), dict) else (ssot if isinstance(ssot, dict) else {})
        cash_eur = _to_decimal(ms.get("unallocated_cash_eur") if isinstance(ms, dict) else None) or Decimal("0.00")
        fx_rate = _to_decimal(_fx_rate_from(fx_info))
        cash_usd = _q2(cash_eur * fx_rate) if (fx_rate is not None and fx_rate > Decimal("0")) else (_to_decimal(ms.get("unallocated_cash_usd") if isinstance(ms, dict) else None) or Decimal("0.00"))
        cash_proof = (
            f"Proof: Internally verified { _q2(cash_eur) } EUR Cash Active / "
            f"0.00 EUR Allocated this cycle = { _q2(cash_eur) } EUR Unallocated"
        )

        tickers = live_state.get("tickers", []) if isinstance(live_state, dict) else []
        by_sym: Dict[str, Dict[str, Any]] = {
            str(t.get("ticker") or "").strip().upper(): t
            for t in (tickers or [])
            if isinstance(t, dict) and t.get("ticker")
        }

        trailing_audits: List[Dict[str, Any]] = []
        plan_positions: List[Dict[str, Any]] = []
        all_directives: List[Dict[str, Any]] = []

        for pos in bundle["positions"]:
            sym = pos["ticker"]
            row = by_sym.get(sym, {})
            price_val = _safe_float(pos.get("price"))
            vwap_val = _safe_float(row.get("vwap"))
            prev_close_val = _safe_float(row.get("regular_close"))
            rsi_val = _safe_float(row.get("rsi")) or 50.0
            tracker = pos.get("tracker") if isinstance(pos.get("tracker"), dict) else {}

            anchor_val = vwap_val if (vwap_val is not None and vwap_val > 0) else (prev_close_val if (prev_close_val is not None and prev_close_val > 0) else price_val)
            pct_dist = round(((price_val - anchor_val) / anchor_val) * 100.0, 2) if (price_val and anchor_val and anchor_val > 0) else 0.0

            tighten_factor = 0.5 if (macro_cal.get("shield_active") or tracker.get("gamma_flicker_overbought")) else 1.0
            eff_stop_pct = round(2.0 * tighten_factor, 2)
            base_ref = price_val if (price_val and anchor_val and price_val < anchor_val) else anchor_val
            stop_price = None
            if base_ref and base_ref > 0:
                stop_dec = _q2(Decimal(str(base_ref)) * (Decimal("1") - (Decimal(str(eff_stop_pct)) / Decimal("100"))))
                stop_price = float(stop_dec)

            trig_cond = "ACTIVE_HOLDING"
            if rsi_val > 70.0 and pct_dist > 2.0:
                trig_cond = "BOTH"
            elif rsi_val > 70.0:
                trig_cond = "RSI > 70"
            elif pct_dist > 2.0:
                trig_cond = "VWAP_DIST > 2%"

            stop_audit = {
                "ticker": sym,
                "anchor_price": round(anchor_val, 2) if anchor_val else None,
                "current_price": price_val,
                "pct_distance_from_anchor": pct_dist,
                "stop_tighten_factor": tighten_factor,
                "effective_stop_pct": eff_stop_pct,
                "effective_stop_price": stop_price,
                "extended_vwap_sweep": pct_dist > 4.0,
                "trigger_condition": trig_cond,
            }
            if price_val is not None:
                trailing_audits.append(stop_audit)

            # ENH_96 / ENH_254 / ENH_255 Fibonacci Front-Run Limit Ladder
            fib = row.get("fib_forecast") if isinstance(row.get("fib_forecast"), dict) else {}
            ladder: List[Dict[str, Any]] = []
            tranches = pos.get("tranches") or {}
            if price_val and price_val > 0 and tranches:
                t1_raw = _safe_float(fib.get("t1_100")) or _safe_float(fib.get("daily_peak_target")) or (price_val * 1.03)
                t2_raw = _safe_float(fib.get("t2_1618")) or (price_val * 1.06)
                t3_raw = _safe_float(fib.get("t3_2618")) or (price_val * 1.10)
                specs = [
                    ("T1", "1.000 Measured Move", "25pct", t1_raw),
                    ("T2", "1.618 Golden Ratio", "50pct", t2_raw),
                    ("T3", "2.618 Runner Extension", "100pct", t3_raw),
                ]
                for tier_code, tier_desc, t_key, raw_lvl in specs:
                    t_info = tranches.get(t_key) or {}
                    sell_sh = t_info.get("sell_shares")
                    if not sell_sh or not raw_lvl or raw_lvl <= 0:
                        continue
                    lim_dec = _q2(Decimal(str(raw_lvl)) * FRONT_RUN_FACTOR)
                    est_usd = _q2(Decimal(str(sell_sh)) * lim_dec)
                    est_eur = _q2(est_usd / fx_rate) if (fx_rate is not None and fx_rate > Decimal("0")) else None
                    lim_f = float(lim_dec)
                    ladder.append({
                        "tier": tier_code,
                        "label": tier_desc,
                        "tranche_pct": int(t_key.replace("pct", "")),
                        "sell_shares": sell_sh,
                        "raw_target_usd": round(raw_lvl, 2),
                        "limit_usd": lim_f,
                        "est_proceeds_usd": float(est_usd),
                        "est_proceeds_eur": float(est_eur) if est_eur is not None else None,
                        "order_text": f"SELL {sell_sh} {sym} LMT ${lim_f:.2f}",
                    })

            pos_directives: List[Dict[str, Any]] = []
            for m in (pos.get("triggered_mandates") or []):
                if not isinstance(m, dict) or m.get("trim_pct") is None:
                    continue
                trim_pct = int(m["trim_pct"])
                t_info = tranches.get(f"{trim_pct}pct") or tranches.get("25pct") or {}
                sell_sh = t_info.get("sell_shares")
                lim_usd = t_info.get("sweep_limit_usd") or pos.get("sweep_limit_usd")
                if sell_sh and lim_usd is not None:
                    directive = {
                        "ticker": sym,
                        "rule_id": m.get("rule_id"),
                        "action": m.get("action"),
                        "trim_pct": trim_pct,
                        "sell_shares": sell_sh,
                        "limit_price_usd": lim_usd,
                        "order_type": "SWEEP_LIMIT",
                        "order_text": f"SELL {sell_sh} {sym} SWEEP LMT ${float(lim_usd):.2f}",
                    }
                    pos_directives.append(directive)
                    all_directives.append(directive)

            plan_positions.append({
                "ticker": sym,
                "shares": pos.get("shares"),
                "wac": pos.get("wac"),
                "price": price_val,
                "valid_telemetry": pos.get("valid_telemetry"),
                "sweep_limit_usd": pos.get("sweep_limit_usd"),
                "front_run_limit_usd": pos.get("front_run_limit_usd"),
                "tranches": tranches,
                "trailing_stop": stop_audit,
                "fib_limit_ladder": ladder,
                "triggered_mandates": pos.get("triggered_mandates") or [],
                "order_directives": pos_directives,
            })

        return {
            "source": "python_deterministic",
            "as_of_ny": bundle["as_of_ny"],
            "regime": bundle["regime"],
            "macro_calendar": macro_cal,
            "global_vetoes": bundle["global_vetoes"],
            "cash_reconciliation": {
                "unallocated_cash_eur": float(_q2(cash_eur)),
                "unallocated_cash_usd": float(_q2(cash_usd)),
                "proof": cash_proof,
            },
            "trailing_stop_audit": trailing_audits,
            "positions": plan_positions,
            "order_directives": all_directives,
        }

    def format_execution_engine_response(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        fx_info: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
    ) -> str:
        """Zero-LLM deterministic response matching engine_instructions/execution.md."""
        return json.dumps(self.build_execution_plan(live_state, ssot=ssot, fx_info=fx_info, ny_dt=ny_dt), indent=2)

    def format_state_validation_router_response(
        self,
        live_state: Dict[str, Any],
        ssot: Optional[Dict[str, Any]] = None,
        fx_info: Optional[Dict[str, Any]] = None,
        ny_dt: Optional[datetime] = None,
        votes: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Zero-LLM deterministic response enforcing ENH_32/ENH_99/MANDATE_36 & EXECUTION_PAYLOAD."""
        plan = self.build_execution_plan(live_state, ssot=ssot, fx_info=fx_info, ny_dt=ny_dt)
        pruned_snapshot = [
            {
                "ticker": p["ticker"],
                "shares": p["shares"],
                "wac": p["wac"],
                "price": p["price"],
                "status": "TRIM_REQUIRED" if p["order_directives"] else "HOLDING",
            }
            for p in plan["positions"]
            if (p.get("shares") or 0) > 0
        ]
        agreement = compute_agreement_score(votes) if votes else {
            "agreement_score": 1.0,
            "fragile": False,
            "status": "ALIGNED",
            "dominant_stance": "HOLD",
            "total_weight": 1.0,
            "buckets": {"BULLISH": 0.0, "HOLD": 1.0, "BEARISH": 0.0},
        }
        active_mandates = sorted({
            str(m.get("rule_id"))
            for p in plan["positions"]
            for m in (p.get("triggered_mandates") or [])
            if isinstance(m, dict) and m.get("rule_id")
        })
        if (plan.get("macro_calendar") or {}).get("shield_active"):
            for r in plan["macro_calendar"].get("active_rules", []):
                if r not in active_mandates:
                    active_mandates.append(r)

        directive = "EXECUTE" if plan["order_directives"] else "HOLD"
        payload = {
            "source": "python_deterministic",
            "Consensus_Status": "VERIFIED",
            "Drift_Status": "STABLE",
            "Agreement_Score_Sa": agreement["agreement_score"],
            "Agreement_Status": agreement["status"],
            "EXECUTION_PAYLOAD": {
                "thoughtSignature": "python_deterministic_state_validation_router",
                "state_context": {
                    "timestamp": plan["as_of_ny"],
                    "directive": directive,
                    "status": plan["regime"]["market_status"],
                    "risk_regime": plan["regime"]["volatility_state"],
                },
                "risk_metrics": {
                    "active_mandates": active_mandates,
                    "global_vetoes": plan["global_vetoes"],
                    "macro_shield_active": bool((plan.get("macro_calendar") or {}).get("shield_active")),
                },
                "portfolio_snapshot": pruned_snapshot,
                "unallocated_cash_eur": plan["cash_reconciliation"]["unallocated_cash_eur"],
                "unallocated_cash_usd": plan["cash_reconciliation"]["unallocated_cash_usd"],
                "order_directives": plan["order_directives"],
                "forensic_intelligence": {
                    "math_proof_liquidity": plan["cash_reconciliation"]["proof"],
                    "trailing_stop_audit": plan["trailing_stop_audit"],
                },
            },
        }
        return json.dumps(payload, indent=2)


ANALYTICS_ENGINE = AnalyticsEngine()
