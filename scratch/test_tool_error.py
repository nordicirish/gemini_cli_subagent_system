from google import genai
from google.genai import types

client = genai.Client()

def my_tool():
    """This tool returns a GenerateContentResponse."""
    return client.models.generate_content(model='gemini-2.5-flash', contents='hi')

chat = client.chats.create(
    model='gemini-2.5-flash',
    config=types.GenerateContentConfig(tools=[my_tool])
)

try:
    chat.send_message('Use my_tool')
except Exception as e:
    import traceback
    traceback.print_exc()
