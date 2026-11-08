import os
import sys
import json
import asyncio
import threading
import time

from atomic_io import (
    atomic_write_json,
    atomic_read_json,
    atomic_update_json,
    atomic_write_text,
)
from tool_registry import (
    GLOBAL_TOOL_REGISTRY,
    ALL_VALID_ROLES,
    authorize_financial_mutation,
)

# We import from the existing fetch_stocks to leverage its battle-tested logic
from fetch_stocks import handle_paste, run_daemon
import fetch_stocks


# Suppress stdout specifically for the daemon thread
class ThreadAwareStdout:
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.daemon_thread_id = None

    def write(self, data):
        if self.daemon_thread_id and threading.get_ident() == self.daemon_thread_id:
            return len(data)  # suppress
        try:
            return self.original_stdout.write(data)
        except UnicodeEncodeError:
            # Fallback for Windows terminals that don't support emojis
            return self.original_stdout.write(
                data.encode("ascii", "replace").decode("ascii")
            )

    def flush(self):
        if self.daemon_thread_id and threading.get_ident() == self.daemon_thread_id:
            return
        self.original_stdout.flush()

    def __getattr__(self, name):
        return getattr(self.original_stdout, name)


sys.stdout = ThreadAwareStdout(sys.stdout)

# Start the background data daemon to keep GLOBAL_STATE updated
daemon_thread = threading.Thread(target=run_daemon, daemon=True)
daemon_thread.start()
sys.stdout.daemon_thread_id = daemon_thread.ident


# A mock request to satisfy FastAPI's Request object in handle_paste
class MockRequest:
    def __init__(self, json_data):
        self._json_data = json_data

    async def json(self):
        return self._json_data


def read_ssot() -> str:
    """Reads the current Single Source of Truth (SSoT) state."""
    path = "context/ssot.json"
    if not os.path.exists(path):
        return "{}"
    try:
        data = atomic_read_json(path, default={})
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error reading SSoT: {str(e)}"


def update_ssot(payload_json: str) -> str:
    """
    Takes an EXECUTION_PAYLOAD JSON string and merges it into the local SSoT.
    Enforces financial-state mutation authorization (Stage 1.1 R3).
    """
    authorize_financial_mutation(
        operation_name="update_ssot",
        target_resource="context/ssot.json",
        payload=payload_json,
    )

    try:
        if isinstance(payload_json, str):
            payload_data = json.loads(payload_json)
        elif isinstance(payload_json, dict):
            payload_data = payload_json
        else:
            raise ValueError("payload_json must be a JSON string or dict.")

        # Try intercepting decision log early
        try:
            intercept_and_log_decision(payload_data)
        except PermissionError:
            raise
        except Exception as ie:
            print(f"[System Warning] Decision interception inside update_ssot failed: {ie}")

        req = MockRequest({"payload": json.dumps(payload_data)})

        # Run the async handler synchronously
        res = asyncio.run(handle_paste(req))

        res_body = json.loads(res.body.decode("utf-8"))

        if res_body.get("status") == "success":
            return "SSoT updated successfully."
        else:
            return f"Error updating SSoT: {res_body.get('message')}"

    except PermissionError:
        raise
    except Exception as e:
        return f"Exception in update_ssot: {str(e)}"


def read_trade_lessons() -> str:
    """Reads the historical trade lessons."""
    path = "context/trade_lessons.json"
    if not os.path.exists(path):
        return "[]"
    try:
        data = atomic_read_json(path, default=[])
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error reading trade lessons: {str(e)}"


def update_trade_lessons(lessons_json: str) -> str:
    """
    Appends or updates the trade_lessons.json file with new insights.
    Enforces financial-state mutation authorization (R3) and full read-modify-write locking (R6).
    """
    authorize_financial_mutation(
        operation_name="update_trade_lessons",
        target_resource="context/trade_lessons.json",
        payload=lessons_json,
    )

    path = "context/trade_lessons.json"
    try:
        if isinstance(lessons_json, str):
            new_data = json.loads(lessons_json)
        else:
            new_data = lessons_json

        def _apply_lessons_update(file_content):
            current_data = []
            is_dict_format = False
            if isinstance(file_content, dict) and "trade_lessons" in file_content:
                current_data = list(file_content["trade_lessons"])
                is_dict_format = True
            elif isinstance(file_content, list):
                current_data = list(file_content)

            if isinstance(new_data, list):
                current_data.extend(new_data)
            elif isinstance(new_data, dict) and "trade_lessons" in new_data:
                current_data.extend(new_data["trade_lessons"])
            else:
                current_data.append(new_data)

            return {"trade_lessons": current_data} if is_dict_format else current_data

        atomic_update_json(path, _apply_lessons_update, default_factory=list)
        return "Trade lessons updated successfully."
    except PermissionError:
        raise
    except Exception as e:
        return f"Error updating trade lessons: {str(e)}"


def read_decision_log() -> str:
    """Reads the continuous time-series ledger of all Council decisions (decision_log.json)."""
    path = "context/decision_log.json"
    if not os.path.exists(path):
        return "[]"
    try:
        data = atomic_read_json(path, default=[])
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error reading decision log: {str(e)}"


def intercept_and_log_decision(data_input) -> None:
    """
    Parses and intercepts decisions and council debates from incoming data
    and appends them to decision_log.json under an atomic read-modify-write lock (R3 & R6).
    """
    authorize_financial_mutation(
        operation_name="update_decision_log",
        target_resource="context/decision_log.json",
        payload=data_input,
    )

    try:
        import re
        import datetime

        parsed_payloads = []

        if isinstance(data_input, dict):
            parsed_payloads.append(data_input)
        elif isinstance(data_input, str):
            json_blocks = re.findall(
                r"```json\s*(.*?)\s*```", data_input, re.DOTALL | re.IGNORECASE
            )
            if not json_blocks:
                json_blocks = re.findall(
                    r"```\s*(.*?)\s*```", data_input, re.DOTALL | re.IGNORECASE
                )

            for block in json_blocks:
                try:
                    block_clean = block.strip()
                    if block_clean.startswith("{") or block_clean.startswith("["):
                        parsed_payloads.append(json.loads(block_clean))
                except Exception:
                    pass

            if not parsed_payloads:
                try:
                    start_idx = data_input.find("{")
                    end_idx = data_input.rfind("}")
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        parsed_payloads.append(
                            json.loads(data_input[start_idx : end_idx + 1])
                        )
                except Exception:
                    pass
        else:
            return

        if not parsed_payloads:
            return

        def find_key_recursive(data, target_key):
            if not isinstance(data, dict):
                if isinstance(data, list):
                    for item in data:
                        res = find_key_recursive(item, target_key)
                        if res is not None:
                            return res
                return None
            if target_key in data:
                return data[target_key]
            for k in (
                "EXECUTION_PAYLOAD",
                "mutable_state",
                "state_context",
                "payload",
                "post_execution_state",
            ):
                if k in data:
                    res = find_key_recursive(data[k], target_key)
                    if res is not None:
                        return res
            for val in data.values():
                if isinstance(val, (dict, list)):
                    res = find_key_recursive(val, target_key)
                    if res is not None:
                        return res
            return None

        for payload in parsed_payloads:
            incoming_portfolio = find_key_recursive(payload, "portfolio_snapshot")
            if incoming_portfolio and isinstance(incoming_portfolio, list):
                ts = (
                    find_key_recursive(payload, "timestamp")
                    or datetime.datetime.now().isoformat()
                )
                trigger = (
                    find_key_recursive(payload, "trigger_context") or "ROUTINE_OUTPUT"
                )

                market_context = {
                    "regime": find_key_recursive(payload, "regime")
                    or find_key_recursive(payload, "risk_regime"),
                    "vix_guard": find_key_recursive(payload, "vix_guard"),
                    "portfolio_value_eur": find_key_recursive(
                        payload, "portfolio_total_value_eur"
                    )
                    or find_key_recursive(payload, "total_liquidity_eur"),
                    "unallocated_cash_eur": find_key_recursive(
                        payload, "unallocated_cash_eur"
                    )
                    or find_key_recursive(payload, "remaining_cash_eur"),
                }

                council_debate = find_key_recursive(payload, "council_debate")

                turn_log = {
                    "timestamp": ts,
                    "trigger_context": trigger,
                    "market_context": market_context,
                    "council_debate": council_debate,
                    "decisions": [],
                }

                for item in incoming_portfolio:
                    if isinstance(item, dict):
                        ticker = item.get("ticker")
                        trade_state = item.get("trade_state", item.get("action"))
                        scrutiny_audit = item.get("scrutiny_audit")
                        price = (
                            item.get("price")
                            or item.get("current_price")
                            or item.get("price_at_eval")
                        )

                        if ticker and (trade_state or scrutiny_audit):
                            turn_log["decisions"].append(
                                {
                                    "ticker": ticker,
                                    "timestamp": ts,
                                    "trigger_context": trigger,
                                    "price_at_eval": price,
                                    "trade_state": trade_state,
                                    "scrutiny_audit": scrutiny_audit,
                                }
                            )

                if turn_log["decisions"]:
                    decision_log_file = "context/decision_log.json"
                    appended_flag = {"appended": False}

                    def _update_decision_ledger(existing_content):
                        is_dict = False
                        if isinstance(existing_content, dict) and "decision_log" in existing_content:
                            log_data = list(existing_content["decision_log"])
                            is_dict = True
                        elif isinstance(existing_content, list):
                            log_data = list(existing_content)
                        else:
                            log_data = []

                        duplicate = False
                        if log_data:
                            last_entry = log_data[-1]
                            if (
                                isinstance(last_entry, dict)
                                and last_entry.get("timestamp") == ts
                                and last_entry.get("decisions") == turn_log["decisions"]
                            ):
                                duplicate = True

                        if not duplicate:
                            log_data.append(turn_log)
                            if len(log_data) > 300:
                                log_data = log_data[-300:]
                            appended_flag["appended"] = True

                        return {"decision_log": log_data} if is_dict else log_data

                    atomic_update_json(
                        decision_log_file, _update_decision_ledger, default_factory=list
                    )
                    if appended_flag["appended"]:
                        print(
                            f"[System] Decision Log Appended: {len(turn_log['decisions'])} decisions."
                        )
                    break
    except PermissionError:
        raise
    except Exception as e:
        print(f"[System Error] Decision log interception failure: {e}")


def update_rules(rules_md_content: str) -> str:
    """
    Updates the canonical gem_trading_rules/rules.md file.
    MANDATORY: Requires explicit human owner confirmation bound to this operation and content (Stage 1.1 R2 & R3).
    Agents cannot supply an approval token to self-authorize this call.
    """
    target_path = os.path.join("gem_trading_rules", "rules.md")
    authorize_financial_mutation(
        operation_name="update_rules",
        target_resource=target_path,
        payload=rules_md_content,
        requires_owner_approval=True,
    )
    atomic_write_text(target_path, rules_md_content)
    return "Rules.md updated successfully."


def get_market_data() -> str:
    """
    Returns the latest live market data for all tickers and macro benchmarks.
    This fetches from the background daemon that continuously updates.
    """
    retries = 0
    while not fetch_stocks.GLOBAL_STATE and retries < 15:
        time.sleep(1)
        retries += 1

    state = fetch_stocks.GLOBAL_STATE
    if not state:
        return json.dumps(
            {"error": "Market data daemon is still initializing or failed."}
        )

    data_to_return = {
        "status": state.get("status"),
        "timestamp": state.get("timestamp"),
        "tickers": state.get("tickers"),
    }
    return json.dumps(data_to_return)


def load_forensic_search_prompt(query: str) -> str:
    """Loads the forensic search prompt from prompts/forensic_search_prompt.txt and replaces the query placeholder."""
    prompt_path = "prompts/forensic_search_prompt.txt"
    template = ""
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read().strip()
        except Exception:
            pass
    if not template:
        template = "Search and summarize the following for financial forensic audit: {query}"
    return template.format(query=query)


def perform_web_forensic_search(query: str) -> str:
    """
    Performs a live web search to fetch current market filings (424B, S-3),
    news catalysts, or structural data. Use this for forensic verification.
    """
    try:
        from agent_framework import AntigravityAgentRunner

        runner = AntigravityAgentRunner()
        prompt = load_forensic_search_prompt(query)
        response = runner.run_prompt(prompt, mode="FAST")
        return response.text
    except Exception as e:
        return f"Error performing web search: {str(e)}"


# ---------------------------------------------------------------------------
# Central Tool Registration with GLOBAL_TOOL_REGISTRY
# Enforcing Protocol 03, Protocol 06, and Stage 1.1 R2/R3/R4 runtime permissions
# ---------------------------------------------------------------------------
GLOBAL_TOOL_REGISTRY.register(
    name="read_ssot",
    func=read_ssot,
    description="Reads the current Single Source of Truth (SSoT) portfolio state.",
    classification="READ",
    required_permission="read_portfolio",
    requires_human_approval=False,
    allowed_roles=ALL_VALID_ROLES,
    affects_financial_state=False,
    target_resource="context/ssot.json",
)

GLOBAL_TOOL_REGISTRY.register(
    name="update_ssot",
    func=update_ssot,
    description="Takes an EXECUTION_PAYLOAD JSON string and merges it into the local SSoT.",
    classification="WRITE",
    required_permission="write_portfolio",
    requires_human_approval=False,
    allowed_roles={"Terminal Orchestrator", "State Validation Router", "Context Engine", "Human", "Owner", "System"},
    is_reversible=False,
    affects_financial_state=True,
    parameters={"payload_json": str},
    target_resource="context/ssot.json",
)

GLOBAL_TOOL_REGISTRY.register(
    name="read_trade_lessons",
    func=read_trade_lessons,
    description="Reads historical trade lessons from context/trade_lessons.json.",
    classification="READ",
    required_permission="read_lessons",
    requires_human_approval=False,
    allowed_roles=ALL_VALID_ROLES,
    affects_financial_state=False,
    target_resource="context/trade_lessons.json",
)

GLOBAL_TOOL_REGISTRY.register(
    name="update_trade_lessons",
    func=update_trade_lessons,
    description="Appends or updates historical trade lessons in context/trade_lessons.json.",
    classification="WRITE",
    required_permission="write_lessons",
    requires_human_approval=False,
    allowed_roles={"Terminal Orchestrator", "Context Engine", "Review Engine", "Post-Trade Review Engine", "Human", "Owner", "System"},
    is_reversible=False,
    affects_financial_state=True,
    parameters={"lessons_json": str},
    target_resource="context/trade_lessons.json",
)

GLOBAL_TOOL_REGISTRY.register(
    name="read_decision_log",
    func=read_decision_log,
    description="Reads the continuous time-series ledger of all Council decisions.",
    classification="READ",
    required_permission="read_decision_log",
    requires_human_approval=False,
    allowed_roles=ALL_VALID_ROLES,
    affects_financial_state=False,
    target_resource="context/decision_log.json",
)

GLOBAL_TOOL_REGISTRY.register(
    name="update_rules",
    func=update_rules,
    description="Updates canonical rules.md. Requires explicit local owner confirmation.",
    classification="WRITE",
    required_permission="write_rules",
    requires_human_approval=True,
    allowed_roles={"Human", "Owner", "System", "Context Engine"},
    is_reversible=False,
    affects_financial_state=True,
    parameters={"rules_md_content": str},
    target_resource=os.path.join("gem_trading_rules", "rules.md"),
)

GLOBAL_TOOL_REGISTRY.register(
    name="get_market_data",
    func=get_market_data,
    description="Returns live market data for all tickers and macro benchmarks from background daemon.",
    classification="READ",
    required_permission="read_market",
    requires_human_approval=False,
    allowed_roles=ALL_VALID_ROLES,
    affects_financial_state=False,
    target_resource="GLOBAL_STATE",
)

GLOBAL_TOOL_REGISTRY.register(
    name="perform_web_forensic_search",
    func=perform_web_forensic_search,
    description="Performs a web search to fetch market filings, catalysts, or news for forensic verification.",
    classification="READ",
    required_permission="search_web",
    requires_human_approval=False,
    allowed_roles=ALL_VALID_ROLES,
    affects_financial_state=False,
    parameters={"query": str},
    target_resource="web_search",
)
