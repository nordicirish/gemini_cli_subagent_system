import os
from google import genai
from google.genai import types

client = genai.Client()

def mock_tool(query: str) -> str:
    """A mock tool."""
    return f"Tool result for {query}"

chat = client.chats.create(
    model="gemini-2.0-flash",
    config=types.GenerateContentConfig(
        system_instruction="When asked to use the tool, first explain your plan, then call the tool, then provide a final summary.",
        tools=[mock_tool]
    )
)

print("--- Sending Message ---")
history_len_before = len(chat.history)
response = chat.send_message("Use the mock tool for 'test'")

print(f"\nResponse.text: {response.text}")

print("\n--- History Analysis ---")
new_history = chat.history[history_len_before:]
full_text = ""
for i, msg in enumerate(new_history):
    print(f"Msg {i} [{msg.role}]:")
    for part in msg.parts:
        if part.text:
            print(f"  Text: {part.text[:50]}...")
            full_text += part.text + "\n\n"
        if part.function_call:
            print(f"  Function Call: {part.function_call.name}")
        if part.function_response:
            print(f"  Function Response: {part.function_response.name}")

print("\n--- Aggregated Text ---")
print(full_text.strip())
