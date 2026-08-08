"""
Exemplos de analise sobre os comentarios ja armazenados no ChromaDB.

Uso:
    python analisar.py
"""

from collections import Counter

from storage import get_collection, query_similar


def resumo_por_categoria():
    collection = get_collection()
    all_data = collection.get(include=["metadatas"])

    contagem = Counter()
    for meta in all_data["metadatas"]:
        for categoria in meta["categorias"].split(","):
            if categoria:
                contagem[categoria] += 1

    print("=== Distribuicao de categorias ===")
    for categoria, total in contagem.most_common():
        print(f"  {categoria:30s} {total}")
    print(f"\nTotal de comentarios: {len(all_data['metadatas'])}")


def top_contribuicoes(n=10):
    collection = get_collection()
    all_data = collection.get(include=["metadatas", "documents"])

    pares = list(zip(all_data["metadatas"], all_data["documents"]))
    pares_contribuicao = [
        (meta, doc) for meta, doc in pares if "contribuicao_valor" in meta["categorias"]
    ]
    pares_contribuicao.sort(key=lambda x: x[0]["score_engajamento"], reverse=True)

    print(f"\n=== Top {n} contribuicoes de maior valor ===")
    for meta, doc in pares_contribuicao[:n]:
        print(f"  [{meta['score_engajamento']:.2f}] {doc[:100]}")


def buscar_parecidos(texto: str):
    print(f"\n=== Comentarios parecidos com: '{texto}' ===")
    resultados = query_similar(texto, n_results=5)
    for doc, meta in zip(resultados["documents"][0], resultados["metadatas"][0]):
        print(f"  [{meta['categorias']}] {doc[:100]}")


if __name__ == "__main__":
    resumo_por_categoria()
    top_contribuicoes()
    # exemplo: buscar_parecidos("o video estava com problema de audio")
