"""
Unified Agent Factory for Web Server (`python/web_server.py`) and CLI (`python/main.py`).
Eliminates CLI/Web capability and security drift, centralizes deterministic analytics
engine wiring, and exposes `refresh_analytics` so precomputed engines do not bloat
Orchestrator LLM tool schemas.
"""

from dataclasses import dataclass
import json
from typing import Any, Callable, Dict, List, Optional

try:
    import agent_framework
    from agent_framework import AgentFramework
    import tools
    import fx_rates
    from analytics_engine import ANALYTICS_ENGINE, _thaw
    from persistence import get_persistence_service, strip_ssot_history
    from sec_edgar import fetch_recent_sec_filings
    from tool_registry import ALL_VALID_ROLES, GLOBAL_TOOL_REGISTRY
except ImportError:
    from python import agent_framework
    from python.agent_framework import AgentFramework
    from python import tools
    from python import fx_rates
    from python.analytics_engine import ANALYTICS_ENGINE, _thaw
    from python.persistence import get_persistence_service, strip_ssot_history
    from python.sec_edgar import fetch_recent_sec_filings
    from python.tool_registry import ALL_VALID_ROLES, GLOBAL_TOOL_REGISTRY


def load_live_analytics_context() -> tuple:
    """Loads (live_state, slim_ssot, fx_snapshot, ny_dt) once for deterministic analytics."""
    try:
        try:
            from fetch_stocks import get_global_state_snapshot
        except ImportError:
            from python.fetch_stocks import get_global_state_snapshot
        live_state = _thaw(get_global_state_snapshot())
    except Exception:
        live_state = {}

    ssot_data = live_state.get("local_storage_state") if isinstance(live_state, dict) else None
    if not isinstance(ssot_data, dict):
        try:
            ssot_data = strip_ssot_history(get_persistence_service().get_ssot_data())
        except Exception:
            ssot_data = {}
    else:
        ssot_data = strip_ssot_history(ssot_data)

    fx_snapshot = fx_rates.fx_public(fx_rates.get_eurusd())
    return live_state, ssot_data, fx_snapshot, agent_framework.ny_now()


def refresh_analytics(symbols: str = "") -> str:
    """Re-evaluates all 7 deterministic Python engines (Regime, Strategy, GEX, Rule Enforcer, Execution, Technical Validator, State Router) on demand."""
    live_state, ssot_data, fx_snapshot, ny_dt = load_live_analytics_context()
    res = ANALYTICS_ENGINE.evaluate(
        live_state,
        ssot=ssot_data,
        fx_info=fx_snapshot,
        ny_dt=ny_dt,
    )
    sym_filter = {
        s.strip().upper()
        for s in str(symbols or "").split(",")
        if s.strip()
    }
    if sym_filter and isinstance(res, dict):
        filtered = dict(res)
        for key in ("positions", "watchlist_signals"):
            items = res.get(key)
            if isinstance(items, list):
                filtered[key] = [
                    item for item in items
                    if isinstance(item, dict) and str(item.get("ticker") or "").strip().upper() in sym_filter
                ]
        res = filtered
    return json.dumps(res, separators=(",", ":"), default=str)


if GLOBAL_TOOL_REGISTRY.get("refresh_analytics") is None:
    GLOBAL_TOOL_REGISTRY.register(
        name="refresh_analytics",
        func=refresh_analytics,
        description="Re-evaluates deterministic Regime, Strategy, GEX, Rule Enforcer, Execution, Technical Validator, and State Router analytics on demand.",
        classification="READ",
        required_permission="read_market",
        requires_human_approval=False,
        allowed_roles=ALL_VALID_ROLES,
        affects_financial_state=False,
        parameters={"symbols": str},
        target_resource="ANALYTICS_ENGINE",
    )


@dataclass
class AgentRuntimeBundle:
    macro_sentinel: Any
    research_engine: Any
    macro_narrative_engine: Any
    data_analyst: Any
    state_validation_router: Any
    rule_enforcer_engine: Any
    execution: Any
    gex_engine: Any
    regime_engine: Any
    strategy_engine: Any
    sentiment_engine: Any
    structural_engine: Any
    context_engine: Any
    technical_validator: Any
    post_trade_review: Any
    review_log_engine: Any
    bullish_advocate: Any
    red_team_pessimist: Any
    neutral_structuralist: Any
    refresh_analytics_tool: Any
    council_members: Dict[str, Any]
    ask_council: Any
    terminal_tools: List[Any]
    terminal_instruction: str
    orchestrator_model: str


def build_agent_runtime(
    framework: AgentFramework,
    analytics_context_loader: Optional[Callable[[], tuple]] = None,
    analytics_engine_provider: Optional[Callable[[], Any]] = None,
    sec_fetcher_provider: Optional[Callable[[], Any]] = None,
) -> AgentRuntimeBundle:
    """
    Constructs the canonical sub-agent, deterministic-engine, and Orchestrator toolset
    shared across Web Server and CLI with full Stage 3.3.1 read-only council guardrails.
    """
    ctx_loader = analytics_context_loader or load_live_analytics_context
    get_engine = analytics_engine_provider or (lambda: ANALYTICS_ENGINE)
    get_sec_fetcher = sec_fetcher_provider or (lambda: fetch_recent_sec_filings)

    macro_sentinel = framework.create_agent_tool(
        name="Macro Sentinel",
        file_path="engine_instructions/macro_sentinel.md",
        mode="PRO",
        agent_tools=[tools.read_ssot, tools.perform_web_forensic_search],
    )
    research_engine = framework.create_agent_tool(
        name="Research Engine",
        file_path="engine_instructions/research.md",
        mode="THINKING",
        agent_tools=[tools.perform_web_forensic_search, tools.fetch_sec_filings, tools.run_quant_investigation],
    )
    macro_narrative_engine = framework.create_agent_tool(
        name="Macro Narrative Engine",
        file_path="engine_instructions/macro_narrative_engine.md",
        mode="THINKING",
        agent_tools=[tools.perform_web_forensic_search, tools.read_ssot, tools.get_market_data],
    )
    data_analyst = framework.create_agent_tool(
        name="Data Analyst",
        file_path="engine_instructions/data_analyst.md",
        mode="PRO",
        agent_tools=[tools.perform_web_forensic_search, tools.get_market_data, tools.fetch_sec_filings, tools.run_quant_investigation],
    )

    state_validation_router = framework.create_deterministic_agent_tool(
        name="State Validation Router",
        handler_fn=lambda _q: get_engine().format_state_validation_router_response(*ctx_loader()),
        description="Deterministic Python State & Validation Router enforcing ENH_32 schema integrity, ENH_99 zero-share pruning, MANDATE_36 trailing stop audits, and canonical EXECUTION_PAYLOAD emission.",
    )
    rule_enforcer_engine = framework.create_deterministic_agent_tool(
        name="Rule Enforcer Engine",
        handler_fn=lambda _q: get_engine().format_rule_enforcer_response(
            *ctx_loader(),
            sec_fetcher=lambda sym: get_sec_fetcher()(sym, limit=3),
        ),
        description="Deterministic Python Rule Enforcer evaluating global vetoes, position trim mandates, friction hurdles, and 72h SEC EDGAR dilution filings.",
    )
    execution = framework.create_deterministic_agent_tool(
        name="Execution Engine",
        handler_fn=lambda _q: get_engine().format_execution_engine_response(*ctx_loader()),
        description="Deterministic Python Execution Engine computing MANDATE_36 trailing stop audits, ENH_96 Fib front-run ladders, Decimal FX/cash proofs, and sweep trim tickets.",
    )

    def _gex_handler(_q: str) -> str:
        live_state, ssot_data, _fx, ny_dt = ctx_loader()
        return get_engine().format_gex_engine_response(live_state, ssot_data, ny_dt=ny_dt)

    gex_engine = framework.create_deterministic_agent_tool(
        name="GEX Engine",
        handler_fn=_gex_handler,
        description="Deterministic Python GEX dealer posture, gamma flip proximity, SSR override, and ENH_17_B whiplash analyzer.",
    )
    regime_engine = framework.create_deterministic_agent_tool(
        name="Regime Engine",
        handler_fn=lambda _q: get_engine().format_regime_engine_response(*ctx_loader()),
        description="Deterministic Python Volatility State & Strategy Permissibility routing matrix.",
    )
    strategy_engine = framework.create_deterministic_agent_tool(
        name="Strategy Engine",
        handler_fn=lambda _q: get_engine().format_strategy_engine_response(*ctx_loader()),
        description="Deterministic Python setup classifier and Volatility Regime strategy permissibility validator.",
    )
    sentiment_engine = framework.create_agent_tool(
        name="Sentiment Engine",
        file_path="engine_instructions/sentiment_engine.md",
        mode="GEMMA",
        agent_tools=[tools.perform_web_forensic_search],
    )
    structural_engine = framework.create_agent_tool(
        name="Structural Engine",
        file_path="engine_instructions/structural_engine.md",
        mode="GEMMA",
        agent_tools=[tools.read_ssot, tools.get_market_data, tools.perform_web_forensic_search, tools.fetch_sec_filings],
    )
    context_engine = framework.create_agent_tool(
        name="Context Engine",
        file_path="engine_instructions/context_engine.md",
        mode="GEMMA",
        agent_tools=[
            tools.read_ssot,
            tools.update_ssot,
            tools.read_trade_lessons,
            tools.update_trade_lessons,
            tools.get_market_data,
            tools.perform_web_forensic_search,
            tools.update_rules,
        ],
    )
    technical_validator = framework.create_deterministic_agent_tool(
        name="Technical Validator",
        handler_fn=lambda _q: get_engine().format_technical_validator_response(*ctx_loader()),
        description="Deterministic Python technical alignment, MANDATE_06 Decimal math/FX proofs, and trim tranche validator.",
    )
    post_trade_review = framework.create_agent_tool(
        name="Post-Trade Review Engine",
        file_path="engine_instructions/post_trade_review.md",
        mode="FAST",
        agent_tools=[
            tools.read_decision_log,
            tools.run_decision_backtest,
            tools.read_trade_lessons,
            tools.update_trade_lessons,
            tools.perform_web_forensic_search,
        ],
    )
    review_log_engine = framework.create_agent_tool(
        name="Review Engine",
        file_path="engine_instructions/post_trade_review.md",
        mode="FAST",
        agent_tools=[tools.read_decision_log, tools.run_decision_backtest, tools.read_trade_lessons],
    )
    bullish_advocate = framework.create_agent_tool(
        name="Bullish Advocate",
        file_path="engine_instructions/bullish_gem.md",
        mode="THINKING",
        agent_tools=[tools.perform_web_forensic_search],
    )
    red_team_pessimist = framework.create_agent_tool(
        name="Red Team Pessimist",
        file_path="engine_instructions/red_team_gem.md",
        mode="THINKING",
        agent_tools=[tools.perform_web_forensic_search],
    )
    neutral_structuralist = framework.create_agent_tool(
        name="Neutral Structuralist",
        file_path="engine_instructions/neutral_gem.md",
        mode="GEMMA",
        agent_tools=[tools.perform_web_forensic_search],
    )

    # Strictly read-only deliberative council members (Stage 3.3.1 guardrail)
    council_members = {
        "ask_macro_sentinel": macro_sentinel,
        "ask_research_engine": research_engine,
        "ask_macro_narrative_engine": macro_narrative_engine,
        "ask_data_analyst": data_analyst,
        "ask_rule_enforcer_engine": rule_enforcer_engine,
        "ask_execution_engine": execution,
        "ask_gex_engine": gex_engine,
        "ask_sentiment_engine": sentiment_engine,
        "ask_structural_engine": structural_engine,
        "ask_bullish_advocate": bullish_advocate,
        "ask_red_team_pessimist": red_team_pessimist,
        "ask_neutral_structuralist": neutral_structuralist,
        "ask_technical_validator": technical_validator,
        "ask_regime_engine": regime_engine,
        "ask_strategy_engine": strategy_engine,
    }
    ask_council = framework.create_parallel_council_tool(council_members)

    # Orchestrator toolset: precomputed deterministic engines are collapsed into `refresh_analytics`
    # to eliminate 6 redundant LLM tool schemas while keeping State Validation Router for sequential routing.
    terminal_tools = [
        tools.read_ssot,
        tools.update_ssot,
        tools.read_trade_lessons,
        tools.update_trade_lessons,
        tools.get_market_data,
        tools.read_decision_log,
        tools.run_decision_backtest,
        tools.fetch_sec_filings,
        tools.read_colab_quant_signals,
        tools.run_quant_investigation,
        refresh_analytics,
        macro_sentinel,
        research_engine,
        macro_narrative_engine,
        data_analyst,
        state_validation_router,
        sentiment_engine,
        structural_engine,
        context_engine,
        post_trade_review,
        bullish_advocate,
        red_team_pessimist,
        neutral_structuralist,
        ask_council,
    ]

    terminal_instruction = framework.load_system_instruction("engine_instructions/terminal.md")
    orchestrator_model = framework._resolve_orchestrator()

    return AgentRuntimeBundle(
        macro_sentinel=macro_sentinel,
        research_engine=research_engine,
        macro_narrative_engine=macro_narrative_engine,
        data_analyst=data_analyst,
        state_validation_router=state_validation_router,
        rule_enforcer_engine=rule_enforcer_engine,
        execution=execution,
        gex_engine=gex_engine,
        regime_engine=regime_engine,
        strategy_engine=strategy_engine,
        sentiment_engine=sentiment_engine,
        structural_engine=structural_engine,
        context_engine=context_engine,
        technical_validator=technical_validator,
        post_trade_review=post_trade_review,
        review_log_engine=review_log_engine,
        bullish_advocate=bullish_advocate,
        red_team_pessimist=red_team_pessimist,
        neutral_structuralist=neutral_structuralist,
        refresh_analytics_tool=refresh_analytics,
        council_members=council_members,
        ask_council=ask_council,
        terminal_tools=terminal_tools,
        terminal_instruction=terminal_instruction,
        orchestrator_model=orchestrator_model,
    )
