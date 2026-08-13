"""
Armazena os comentarios classificados no ChromaDB, com embeddings multilingues
(importante porque os comentarios sao majoritariamente em portugues, e o embedding
padrao do Chroma e treinado em ingles).
"""

import chromadb
from chromadb.utils import embedding_functions

COLLECTION_NAME = "youtube_comentarios"
DB_PATH = "./chroma_db"

# Embedding multilingue - roda localmente, sem custo e sem API externa
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Singleton para cachear o embedding model e evitar recarregar a cada chamada
_collection = None


def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=DB_PATH)
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME, embedding_function=embedding_fn
        )
    return _collection


def store_comments(comments: list[dict]) -> None:
    """Insere (ou atualiza) os comentarios classificados no ChromaDB em lotes."""
    collection = get_collection()

    batch_size = 200
    for i in range(0, len(comments), batch_size):
        batch = comments[i : i + batch_size]

        ids = [c["comment_id"] for c in batch]
        documents = [c["text"] for c in batch]
        metadatas = [
            {
                "video_id": c["video_id"],
                "video_title": c.get("video_title", ""),
                "author": c.get("author", ""),
                "like_count": c.get("like_count", 0),
                "published_at": c.get("published_at", ""),
                "is_reply": c.get("parent_id") is not None,
                # Chroma nao aceita listas em metadata escalar simples de forma direta
                # em todas as versoes; salvamos como string separada por virgula.
                "categorias": ",".join(c.get("categorias", [])),
                "score_engajamento": c.get("score_engajamento", 0.0),
            }
            for c in batch
        ]

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        print(f"  Armazenados {min(i + batch_size, len(comments))}/{len(comments)} comentarios")

    print(f"\nTotal na coleção '{COLLECTION_NAME}': {collection.count()} comentarios")


def query_similar(text: str, n_results: int = 5, where: dict | None = None):
    """Busca comentarios semanticamente parecidos com 'text', opcionalmente filtrando por metadata."""
    collection = get_collection()
    return collection.query(query_texts=[text], n_results=n_results, where=where)
