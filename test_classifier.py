"""
Testes do classificador: regras rapidas, dedup, validacao de categorias e fallback.
Rodar com: pytest test_classifier.py -v
"""

from unittest.mock import MagicMock, patch

import classifier as cl


# --------------------------------------------------------------------------
# quick_classify
# --------------------------------------------------------------------------

def test_quick_classify_emoji_isolado():
    assert cl.quick_classify("👏👏👏") == ["sem_conteudo_classificavel"]


def test_quick_classify_kkk():
    assert cl.quick_classify("kkkkkk") == ["sem_conteudo_classificavel"]


def test_quick_classify_agradecimento_curto():
    assert cl.quick_classify("muito obrigado!") == ["agradecimento"]


def test_quick_classify_agradecimento_com_negacao_vai_para_llm():
    assert cl.quick_classify("não, obrigado") is None


def test_quick_classify_ambiguo_retorna_none():
    assert cl.quick_classify("esse video mudou minha forma de pensar sobre o assunto") is None


# --------------------------------------------------------------------------
# _validate_categorias
# --------------------------------------------------------------------------

def test_validate_categorias_mantem_validas():
    assert cl._validate_categorias(["elogio_generico", "pergunta_duvida"]) == ["elogio_generico", "pergunta_duvida"]


def test_validate_categorias_remove_invalidas():
    assert cl._validate_categorias(["categoria_inventada", "agradecimento"]) == ["agradecimento"]


def test_validate_categorias_todas_invalidas_cai_no_default():
    assert cl._validate_categorias(["nao_existe"]) == ["sem_conteudo_classificavel"]


def test_validate_categorias_lista_vazia_cai_no_default():
    assert cl._validate_categorias([]) == ["sem_conteudo_classificavel"]


# --------------------------------------------------------------------------
# classify_comments: dedup + fallback + checkpoint resume
# --------------------------------------------------------------------------

def test_classify_comments_propaga_resultado_para_grupo_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CHECKPOINT_PATH", str(tmp_path / "checkpoint.json"))
    monkeypatch.setattr(cl, "BATCH_SLEEP_SECONDS", 0)
    comments = [
        {"comment_id": "c1", "text": "conteudo unico interessante sobre o tema"},
        {"comment_id": "c2", "text": "conteudo unico interessante sobre o tema"},  # duplicata exata
    ]
    with patch.object(cl, "Groq") as MockGroq:
        client = MagicMock()
        MockGroq.return_value = client
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content='{"resultados": [{"id": "c1", "categorias": ["contribuicao_valor"], "score_engajamento": 0.8}]}'))
        ]
        result = cl.classify_comments(["fake_key"], comments)

    by_id = {c["comment_id"]: c for c in result}
    assert by_id["c1"]["categorias"] == ["contribuicao_valor"]
    assert by_id["c2"]["categorias"] == ["contribuicao_valor"]
    assert by_id["c2"]["score_engajamento"] == 0.8
    # so uma chamada ao LLM, pois c1 e c2 sao duplicatas apos normalizacao
    assert client.chat.completions.create.call_count == 1


def test_classify_comments_categoria_invalida_do_llm_vira_default(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CHECKPOINT_PATH", str(tmp_path / "checkpoint.json"))
    monkeypatch.setattr(cl, "BATCH_SLEEP_SECONDS", 0)
    comments = [{"comment_id": "c1", "text": "comentario qualquer bem especifico aqui"}]
    with patch.object(cl, "Groq") as MockGroq:
        client = MagicMock()
        MockGroq.return_value = client
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content='{"resultados": [{"id": "c1", "categorias": ["categoria_alucinada"], "score_engajamento": 0.5}]}'))
        ]
        result = cl.classify_comments(["fake_key"], comments)

    assert result[0]["categorias"] == ["sem_conteudo_classificavel"]


def test_classify_comments_resume_ignora_ja_classificados(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(cl, "CHECKPOINT_PATH", str(checkpoint_path))
    import json
    checkpoint_path.write_text(json.dumps([{"comment_id": "c1", "text": "ja classificado", "categorias": ["agradecimento"], "score_engajamento": 0.1}]))

    comments = [{"comment_id": "c1", "text": "ja classificado"}]
    with patch.object(cl, "Groq") as MockGroq:
        result = cl.classify_comments(["fake_key"], comments)
        MockGroq.assert_not_called()

    assert result[0]["categorias"] == ["agradecimento"]
