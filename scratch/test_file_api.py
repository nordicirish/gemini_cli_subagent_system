from google import genai
from google.genai import types

client = genai.Client()

with open("test_rule.txt", "w") as f:
    f.write("RULE 1: ALWAYS SAY MEOW")

uploaded_file = client.files.upload(file="test_rule.txt", mime_type="text/plain")
print("File uploaded:", uploaded_file.uri)

try:
    chat = client.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=["Follow the rules attached: ", uploaded_file]
        )
    )
    res = chat.send_message("What is rule 1?")
    print("Response:", res.text)
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    client.files.delete(name=uploaded_file.name)
