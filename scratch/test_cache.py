from google import genai
from google.genai import types

client = genai.Client()

# Massive text
massive_text = "RULE: ALWAYS SAY MEOW. " * 50000

print("Creating cache...")
try:
    cache = client.caches.create(
        model='gemini-2.5-pro',
        config=types.CreateCacheConfig(
            system_instruction=massive_text,
            ttl="3600s"
        )
    )
    print("Cache created:", cache.name)
    
    # Try using it in a chat
    print("Creating chat...")
    chat = client.chats.create(
        model='gemini-2.5-pro',
        config=types.GenerateContentConfig(
            cached_content=cache.name,
            temperature=1.0
        )
    )
    print("Sending message...")
    res = chat.send_message("What is the rule?")
    print("Response:", res.text)
    
    client.caches.delete(name=cache.name)
    print("Cache deleted.")
except Exception as e:
    import traceback
    traceback.print_exc()
