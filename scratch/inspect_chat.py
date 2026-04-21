from google import genai
client = genai.Client()
chat = client.chats.create(model="gemini-2.0-flash")
print(dir(chat))
