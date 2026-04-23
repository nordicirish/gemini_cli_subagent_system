from google import genai
from google.genai import types

client = genai.Client()

def bad_tool():
    """This tool raises a RuntimeError."""
    raise RuntimeError("All models failed. Last error: 429 RESOURCE_EXHAUSTED. {'error': '...'}")

chat = client.chats.create(
    model='gemini-2.5-flash',
    config=types.GenerateContentConfig(tools=[bad_tool])
)

try:
    chat.send_message('Use bad_tool')
except Exception as e:
    import traceback
    traceback.print_exc()
