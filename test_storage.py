"""
Testes de storage: metadata montada e singleton do embedding model.
Rodar com: pytest test_storage.py -v
"""

from unittest.mock import MagicMock, patch

import storage as st


def setup_function():
    st._collection = None


def test_get_collection_reusa_instancia_entre_chamadas():
    with patch.object(st.chromadb, "PersistentClient") as MockClient, \
         patch.object(st.embedding_functions, "SentenceTransformerEmbeddingFunction") as MockEmbedFn:
        MockClient.return_value.get_or_create_collection.return_value = MagicMock()

        c1 = st.get_collection()
        c2 = st.get_collection()

        assert c1 is c2
        assert MockClient.call_count == 1
        assert MockEmbedFn.call_count == 1


def test_store_comments_monta_metadata_correta():
    comments = [
        {
            "comment_id": "c1",
            "video_id": "v1",
            "video_title": "Titulo",
            "author": "Fulano",
            "text": "texto do comentario",
            "like_count": 3,
            "published_at": "2024-01-01",
            "parent_id": "c0",
            "categorias": ["agradecimento", "elogio_generico"],
            "score_engajamento": 0.3,
        }
    ]
    fake_collection = MagicMock()
    fake_collection.count.return_value = 1
    with patch.object(st, "get_collection", return_value=fake_collection):
        st.store_comments(comments)

    _, kwargs = fake_collection.upsert.call_args
    assert kwargs["ids"] == ["c1"]
    assert kwargs["metadatas"][0]["categorias"] == "agradecimento,elogio_generico"
    assert kwargs["metadatas"][0]["is_reply"] is True
