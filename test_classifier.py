import os
from classifier import classify_comments
from dotenv import load_dotenv

load_dotenv()

comments = [{"comment_id": "test_id", "text": "Este é um teste de classificação."}]
groq_api_key = os.getenv("GROQ_API_KEY")
groq_api_key_2 = os.getenv("GROQ_API_KEY_2")
groq_api_keys = [groq_api_key] + ([groq_api_key_2] if groq_api_key_2 else [])

try:
    results = classify_comments(groq_api_keys, comments)
    print("Resultado:", results)
except Exception as e:
    print(f"Erro capturado: {e}")
    import traceback
    traceback.print_exc()
