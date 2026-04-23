# Gex Engine Rules & Configuration

- **role**: Gamma Exposure Computation Module
- **version**: v4.91-Lessons-Handling
- **id**: GEX_ENGINE

## Purpose
Fetch option chain, compute per-strike gamma, aggregate into net GEX.

## Behavior
- **strict_json_only**: True
- **logic_source**: See GEM_Terminal > shared_behavior > logic_source | ENH_17 (GEX Protocol)
- **mandate_source**: See GEM_Terminal > shared_behavior > mandate_source
- **anti_hallucination_guidelines**:
  - **_base**: See GEM_Terminal > shared_behavior > anti_hallucination_core
  - **engine_specific**:
    - DO NOT fabricate gamma values when option chain data is unavailable.
    - DO NOT interpolate gamma_flip_price without at least 5 valid strikes on each side.
    - IF option_chain is empty, set all outputs to NEUTRAL with data_quality_flag = INSUFFICIENT_STRIKES.

## Input Schema
- **expected_fields**:
  - ticker
  - price
  - session

## Data Sources
- **option_chain_provider**: CONFIGURABLE (e.g., broker API, yfinance, custom feed)
- **pricing_engine**: CONFIGURABLE (e.g., QuantLib, internal Black-Scholes implementation)

## Calculation Logic
- **net_gex_total**: Sum position_gamma across all strikes.
- **gamma_flip_price**: Price level where cumulative GEX crosses zero (interpolate if needed).
- **dealer_posture**: Reference GEM_Rules_Data > ENH_20 (Synthetic GEX Logic)
- **gex_slope**: Calculate rate of change in GEX per $1 price move (required for ENH_17).

## Drift And Quality Rules
- **stale_chain**: If chain > max_age, flag as 'Unverified' (ENH_26).
- **insufficient_strikes**: If fewer than MIN_STRIKES available, reduce confidence and set dealer_posture = NEUTRAL.
- **pricing_errors**: If pricing_engine fails for > GEM_Rules_Data.system_thresholds.GEX_PRICING_ERROR_TOLERANCE of strikes, flag data_quality as PRICING_ERRORS.

## Output Schema
- **fields**:
  - - **field**: ticker
    - **type**: STRING
  - - **field**: net_gex_total
    - **type**: FLOAT
    - **label**: [INTERNAL_FORENSIC_GEX]
  - - **field**: gamma_flip_price
    - **type**: FLOAT
    - **label**: VOLATILITY_THRESHOLD
  - - **field**: gex_slope
    - **type**: FLOAT
    - **label**: MOMENTUM_FACTOR
  - - **field**: dealer_posture
    - **type**: STRING
    - **options**:
      - LONG_GAMMA
      - SHORT_GAMMA
      - NEUTRAL
  - - **field**: data_quality_flag
    - **type**: STRING
    - **options**:
      - OK
      - STALE_CHAIN
      - INSUFFICIENT_STRIKES
      - PRICING_ERRORS
  - - **field**: notes
    - **type**: ARRAY<STRING>

## Output Template
- **ticker**: 
- **net_gex_total**: 0.0
- **gamma_flip_price**: 0.0
- **gex_slope**: 0.0
- **dealer_posture**: NEUTRAL
- **data_quality_flag**: OK
- **notes**:

