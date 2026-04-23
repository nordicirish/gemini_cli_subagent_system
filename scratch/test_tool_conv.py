from google import genai
from google.genai import _common

def my_tool(a: int) -> str:
    """Docs"""
    return "hello"

try:
    # See if there's a helper to convert
    tools = _common.get_custom_mapping().get('tools', None)
    print("Tools mapping:", tools)
except Exception as e:
    import traceback
    traceback.print_exc()
