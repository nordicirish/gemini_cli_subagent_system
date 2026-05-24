from google import genai
from google.genai import types

client = genai.Client()

def mock_tool(arg1: str) -> str:
    """Mock tool description.
    
    Args:
        arg1: Argument description.
    """
    return arg1

print("Testing with client parameter:")
try:
    fd = types.FunctionDeclaration.from_callable(client=client, callable=mock_tool)
    print("FunctionDeclaration creation success:", fd)
    
    print("\nWrapping in types.Tool:")
    tool = types.Tool(function_declarations=[fd])
    print("Tool creation success:", tool)
    
    print("\nPutting in CreateCachedContentConfig:")
    config = types.CreateCachedContentConfig(
        tools=[tool],
        display_name="test_cache",
        contents=[types.Content(role="user", parts=[types.Part(text="hello")])],
    )
    print("CreateCachedContentConfig creation success!")
except Exception as e:
    print("Failed:", e)
