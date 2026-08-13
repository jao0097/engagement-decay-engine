"""
Pipeline completo: extrai comentarios de um canal do YouTube, classifica por
engajamento via Groq (LLM open-source), e armazena no ChromaDB.

Uso:
    python main.py <CHANNEL_ID>

O CHANNEL_ID e o identificador do canal (comeca com "UC..."), nao o @handle.
Para descobrir: abra o canal no YouTube -> ... -> "Compartilhar canal" -> "Copiar ID do canal",
ou use https://commentpicker.com/youtube-channel-id.php
"""

import json
import os
import sys

from dotenv import load_dotenv

from classifier import classify_comments
from storage import store_comments
from youtube_extractor import extract_channel_comments

load_dotenv()

RAW_COMMENTS_PATH = "./data/comentarios_brutos.json"
CLASSIFIED_COMMENTS_PATH = "./data/comentarios_classificados.json"
DEFAULT_CHANNEL_ID = "UC1Nm7gQCcGvgLyVcGTXp-Ww"


def _parse_args(argv: list[str]) -> tuple[str, bool]:
    """Retorna (channel_id, reclassify) a partir de sys.argv[1:]."""
    reclassify = "--reclassify" in argv
    positional = [a for a in argv if not a.startswith("--")]
    channel_id = positional[0] if positional else DEFAULT_CHANNEL_ID
    return channel_id, reclassify


def main():
    channel_id, reclassify = _parse_args(sys.argv[1:])
    if channel_id == DEFAULT_CHANNEL_ID:
        print(f"Nenhum CHANNEL_ID especificado. Usando o canal padrão do sistema: {channel_id}")

    youtube_api_key = os.getenv("YOUTUBE_API_KEY")
    groq_api_key = os.getenv("GROQ_API_KEY")
    groq_api_key_2 = os.getenv("GROQ_API_KEY_2")

    if not youtube_api_key or not groq_api_key:
        print("Erro: defina YOUTUBE_API_KEY e GROQ_API_KEY no arquivo .env")
        sys.exit(1)

    groq_api_keys = [groq_api_key] + ([groq_api_key_2] if groq_api_key_2 else [])

    # Etapa 1: extracao (pula se o staging ja existir, para nao gastar cota de novo)
    if os.path.exists(RAW_COMMENTS_PATH):
        print(f"Arquivo {RAW_COMMENTS_PATH} ja existe, pulando extracao.")
        print("(apague o arquivo se quiser re-extrair do YouTube)\n")
        with open(RAW_COMMENTS_PATH, encoding="utf-8") as f:
            comments = json.load(f)
    else:
        comments = extract_channel_comments(youtube_api_key, channel_id, RAW_COMMENTS_PATH)

    if not comments:
        print("Nenhum comentario encontrado. Encerrando.")
        return

    # Etapa 2: classificacao (pula se ja existe, a menos que --reclassify seja passado)
    if os.path.exists(CLASSIFIED_COMMENTS_PATH) and not reclassify:
        print(f"\nArquivo {CLASSIFIED_COMMENTS_PATH} ja existe, pulando classificacao.")
        print("(passe --reclassify para forcar reclassificacao)\n")
        with open(CLASSIFIED_COMMENTS_PATH, encoding="utf-8") as f:
            classified = json.load(f)
    else:
        print("\n=== Classificando comentarios ===")
        classified = classify_comments(groq_api_keys, comments)

        os.makedirs(os.path.dirname(CLASSIFIED_COMMENTS_PATH), exist_ok=True)
        with open(CLASSIFIED_COMMENTS_PATH, "w", encoding="utf-8") as f:
            json.dump(classified, f, ensure_ascii=False, indent=2)
        print(f"Classificacao salva em: {CLASSIFIED_COMMENTS_PATH}")

    # Etapa 3: armazenamento no ChromaDB
    print("\n=== Armazenando no ChromaDB ===")
    store_comments(classified)

    print("\nPipeline concluido com sucesso.")


if __name__ == "__main__":
    main()
