from google import genai
from google.genai import types

client = genai.Client()

def my_tool():
    """This tool says hello."""
    return 'hello'

try:
    cache = client.caches.create(
        model='gemini-2.5-pro',
        config=types.CreateCachedContentConfig(
            system_instruction="Follow rules.",
            ttl="3600s"
        )
    )
    print("Cache created:", cache.name)
    
    chat = client.chats.create(
        model='gemini-2.5-pro',
        config=types.GenerateContentConfig(
            cached_content=cache.name,
            temperature=1.0,
            tools=[my_tool]
        )
    )
    
    res = chat.send_message("Use my_tool")
    print(res.text)
    
    client.caches.delete(name=cache.name)
except Exception as e:
    import traceback
    traceback.print_exc()
