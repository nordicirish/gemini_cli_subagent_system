import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from web_server import get_chart_parts
from agent_framework import AgentFramework

def test_live_call():
    chart_parts, chart_symbols = get_chart_parts()
    print(f"Testing with chart symbols: {chart_symbols}")
    framework = AgentFramework()
    prompt_text = "Confirm that you received the chart image(s) for: " + ", ".join(chart_symbols)
    payload = [*chart_parts, prompt_text]
    res = framework.client.models.generate_content(
        model="gemini-2.5-flash",
        contents=payload
    )
    print("Gemini Response:\n", res.text)

if __name__ == "__main__":
    test_live_call()
