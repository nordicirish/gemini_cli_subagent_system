{
  "_meta": {
    "description": "FINAL EOD SYNCHRONIZATION - V6.2 ADVERSARIAL",
    "is_simulation": false,
    "source": "Council Consolidated Feed",
    "reliability": "High",
    "sync_id": "2026-04-24-EOD-SYNC-01",
    "protocol_version": "v6.2-MD"
  },
  "timestamp": "2026-04-24 23:59:59",
  "status": "CLOSED",
  "market_context": {
    "vix": 18.71,
    "vvix": 106.42,
    "spy_rsi": 70.46,
    "risk_regime": "AUCTION_TAIL_RECONCILIATION",
    "alpha_sentry_mode": "PREEMPTIVE_DEFENSE"
  },
  "tickers": [
    {
      "ticker": "UMAC",
      "price": 14.79,
      "vwap": 14.71,
      "rsi": 49.2,
      "atr_percent": 11.5,
      "net_gex_total": 0.003395,
      "dealer_posture": "LONG_GAMMA",
      "score": 8,
      "trend": "FLAT",
      "signal": "RECLAIMED_VWAP",
      "ssr_active": true
    },
    {
      "ticker": "DFTX",
      "price": 22.53,
      "vwap": 22.45,
      "rsi": 61.37,
      "atr_percent": 5.99,
      "net_gex_total": 0.004544,
      "dealer_posture": "LONG_GAMMA",
      "score": 7,
      "trend": "UP",
      "signal": "RS_LEADER",
      "ssr_active": false
    },
    {
      "ticker": "RCAT",
      "price": 11.86,
      "vwap": 11.99,
      "rsi": 43.27,
      "net_gex_total": -0.00459,
      "dealer_posture": "SHORT_GAMMA",
      "score": -2,
      "trend": "FLAT",
      "signal": "WATCHING"
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
        "status": "HOLD / SSR_PROTECTED",
        "trade_state": "LONG",
        "hard_catalyst": "Success Memo (10 U.S.C. § 4022)",
        "conviction_sa": 0.81
      },
      {
        "ticker": "DFTX",
        "shares": 390,
        "wac": 19.09,
        "status": "HOLD / RS_LEADER",
        "trade_state": "LONG",
        "hard_catalyst": "Voyage Phase 3 / CPV Override",
        "conviction_sa": 0.92
      }
    ]
  },
  "forensic_intelligence": {
    "active_flags": [
      "ENH_72_PREEMPTIVE_DEFENSE_ACTIVE",
      "ENH_73_SUCCESS_MEMO_ANCHOR",
      "ENH_74_NOON_SPIKE_VETO_PREP",
      "SSR_CIRCUIT_BREAKER_ACTIVE_MON"
    ],
    "structural_triggers": [
      "Rule 201 Short Sale Restriction effective until 2026-04-27 16:00 EST.",
      "MANDATE_18: Nordea_ESA Tax-Neutral Rotation Protocol Active.",
      "10 U.S.C. § 4022: Statutory Production Bridge Verified."
    ],
    "risk_summary": "VVIX divergence requires tight stop maintenance on Monday morning. Yield shock from 2Y/5Y auctions remains the primary systemic threat."
  }
}