import os
import sys
from google import genai
from google.genai import types

# Make sure we can import from workspace root
sys.path.insert(0, os.path.abspath("."))

import tools
from agent_framework import AgentFramework

print("Initializing framework...")
framework = AgentFramework()

# Let's list some of the tools
import web_server

print("Web Server Tools count:", len(web_server.all_tools))

# Let's try to convert each tool and construct CreateCachedContentConfig
formatted_tools = []
for t in web_server.all_tools:
    if isinstance(t, dict) and "google_search" in t:
        formatted_tools.append(types.Tool(google_search=types.GoogleSearch()))
    elif isinstance(t, types.Tool):
        formatted_tools.append(t)
    elif callable(t):
        print(f"Converting callable: {t.__name__}")
        fd = types.FunctionDeclaration.from_callable(client=framework.client, callable=t)
        formatted_tools.append(types.Tool(function_declarations=[fd]))
    else:
        formatted_tools.append(t)

print("Formatted tools length:", len(formatted_tools))

# Try creating a minimal cache to see if the Gemini API accepts the whole config!
print("\nAttempting to create a real context cache via Gemini API...")
try:
    # Use a tiny content string to save tokens
    test_content = "## TEST CANONICAL RULES\nThis is a test of tool cache integration."
    
    # We can try creating the cache
    target_model = framework._get_cloud_models("PRO")[0]
    print(f"Target model for cache: {target_model}")
    
    cache = framework.client.caches.create(
        model=target_model,
        config=types.CreateCachedContentConfig(
            system_instruction="You are a helpful assistant.",
            tools=formatted_tools,
            display_name="test_live_tool_conversion",
            contents=[types.Content(role="user", parts=[types.Part(text=test_content)])],
            ttl="300s",  # 5-minute short TTL for test
        ),
    )
    print("SUCCESS! Created context cache:", cache.name)
    
    # Clean up the cache to be a good citizen
    print("Cleaning up test cache...")
    framework.client.caches.delete(name=cache.name)
    print("Cleanup success!")
    
except Exception as e:
    print("FAILED to create live cache on Gemini:", e)
