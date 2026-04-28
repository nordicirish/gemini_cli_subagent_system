{
  "_meta": {
    "description": "FINAL EOD SYNCHRONIZATION",
    "is_simulation": false,
    "source": "Council Consolidated Feed",
    "reliability": "High",
    "sync_id": "2026-04-27-EOD-SYNC-02",
    "protocol_version": "v6.8-MD-Enhanced"
  },
  "timestamp": "2026-04-27 16:15:00",
  "status": "CLOSED",
  "market_context": {
    "vix": 18.15,
    "vvix": 97.79,
    "spy_rsi": 70.98,
    "risk_regime": "NORMAL",
    "alpha_sentry_mode": "PREEMPTIVE_DEFENSE"
  },
  "tickers": [
    {
      "ticker": "UMAC",
      "price": 14.82,
      "vwap": 14.5595,
      "rsi": 50.1,
      "atr_percent": 10.86,
      "net_gex_total": 0.001324,
      "dealer_posture": "LONG_GAMMA",
      "score": 4,
      "trend": "FLAT",
      "signal": "NEUTRAL",
      "ssr_active": false
    },
    {
      "ticker": "DFTX",
      "price": 20.98,
      "vwap": 21.5415,
      "rsi": 49.46,
      "atr_percent": 6.68,
      "net_gex_total": 0.003718,
      "dealer_posture": "LONG_GAMMA",
      "score": 0,
      "trend": "FLAT",
      "signal": "NEUTRAL",
      "ssr_active": false
    },
    {
      "ticker": "RCAT",
      "price": 11.69,
      "vwap": 11.5384,
      "rsi": 42.72,
      "atr_percent": 11.37,
      "net_gex_total": -0.000644,
      "dealer_posture": "SHORT_GAMMA",
      "score": 0,
      "trend": "FLAT",
      "signal": "NEUTRAL",
      "ssr_active": false
    }
  ],
  "mutable_state": {
    "unallocated_cash_eur": 0,
    "total_liquidity_eur": 50509.29,
    "portfolio_snapshot": [
      {
        "ticker": "UMAC",
        "shares": 2821,
        "wac": 13.31,
        "status": "HOLD",
        "trade_state": "LONG",
        "hard_catalyst": "Success Memo (10 U.S.C. § 4022) / Norway Drone Pact",
        "conviction_sa": 0.85
      },
      {
        "ticker": "DFTX",
        "shares": 390,
        "wac": 19.09,
        "status": "HOLD / RS_LEADER",
        "trade_state": "LONG",
        "hard_catalyst": "White House Policy Signal / Stifel $30 PT",
        "conviction_sa": 0.95
      }
    ]
  },
  "forensic_intelligence": {
    "active_flags": [
      "ENH_72_PREEMPTIVE_DEFENSE_ACTIVE"
    ],
    "structural_triggers": [
      "Rule 201 Short Sale Restriction EXPIRED for UMAC at 16:00 EST."
    ],
    "risk_summary": "EOD Forensic Audit: UMAC absorbed morning flush and closed at 14.82 (+1.16%). SSR protection has officially expired. DFTX stabilized at 20.98 post yield-shock. Market broadly overbought (SPY RSI 70.98) with collapsing VIX (18.15). Positions carried overnight."
  },
  "runtime_flags": {
    "agent_votes": [
      {
        "agent": "BULLISH_ADVOCATE",
        "vote_weight": 0.34,
        "bias_flag": "MOMENTUM_BIAS",
        "self_critique_string": "Assuming LONG_GAMMA overrides the lack of SSR bid protection in the event of an aggressive short-selling attack at tomorrow's open."
      },
      {
        "agent": "RED_TEAM_PESSIMIST",
        "vote_weight": 0.33,
        "bias_flag": "RISK_AVERSION_BIAS",
        "self_critique_string": "Overweighting the removal of SSR as a guaranteed short-selling catalyst, ignoring the asset's confirmed Relative Strength (RS) divergence."
      },
      {
        "agent": "NEUTRAL_STRUCTURALIST",
        "vote_weight": 0.33,
        "bias_flag": "PASSIVE_BIAS",
        "self_critique_string": "Processing state updates mechanically without dynamically adjusting the agent conviction_sa scores based on the lost structural protection."
      }
    ]
  },
  "macro_calendar_shield": "INACTIVE"
}