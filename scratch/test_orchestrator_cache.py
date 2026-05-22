from agent_framework import AgentFramework
import os

framework = AgentFramework()

# Create cache
framework.setup_context_cache(model="gemini-2.5-flash", subagent_files=["terminal.md"])

cache_to_use = None
if getattr(framework, "cached_content_name", None):
    cache_to_use = framework.cached_content_name
    print(f"[System] Binding Context Cache to chat session: {cache_to_use}")
else:
    print("[System] No cache to bind.")

try:
    chat = framework.client.chats.create(
        model="gemini-2.5-flash",
        config={"cached_content": cache_to_use}
    )
    print("Chat session successfully created with cache:", cache_to_use)
except Exception as e:
    print("Failed to create chat session:", e)
