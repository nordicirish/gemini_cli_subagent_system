from google import genai
client = genai.Client()
models = [m.name for m in client.models.list()]
with open('models_list.txt', 'w') as f:
    f.write('\n'.join(models))
print(f"Wrote {len(models)} models to models_list.txt")
