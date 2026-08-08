import json
import os
import sys
from dotenv import load_dotenv
from classifier import classify_comments

load_dotenv()

RAW_COMMENTS_PATH = "./data/comentarios_brutos.json"

def resume_classification():
    groq_api_key = os.getenv("GROQ_API_KEY")
    groq_api_key_2 = os.getenv("GROQ_API_KEY_2")
    if not groq_api_key:
        print("Erro: GROQ_API_KEY nao encontrada no .env")
        sys.exit(1)
    groq_api_keys = [groq_api_key] + ([groq_api_key_2] if groq_api_key_2 else [])

    print("Carregando comentarios brutos...")
    with open(RAW_COMMENTS_PATH, encoding="utf-8") as f:
        comments = json.load(f)

    print("Iniciando/resumindo classificacao...")
    classified = classify_comments(groq_api_keys, comments)

    output_path = "./data/comentarios_classificados.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)
    print(f"Classificacao concluida e salva em {output_path}")

if __name__ == "__main__":
    resume_classification()
