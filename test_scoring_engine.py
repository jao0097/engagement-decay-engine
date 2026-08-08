"""
Testes dos scorers de qualidade textual. Rodar com: pytest test_scoring_engine.py -v
"""

import pytest

from scoring_engine import CategoryWeightedScorer, HeuristicScorer


class TestHeuristicScorer:
    def setup_method(self):
        self.scorer = HeuristicScorer()

    def test_texto_vazio_score_zero(self):
        assert self.scorer.score({"text": ""}) == 0.0

    def test_apenas_emoji_score_zero(self):
        assert self.scorer.score({"text": "😂😂😂"}) == 0.0

    def test_risada_curta_score_zero(self):
        assert self.scorer.score({"text": "kkkkkk"}) == 0.0

    def test_comentario_longo_e_diverso_score_alto(self):
        texto = (
            "Achei excelente, mas vale notar que na versao mais nova esse "
            "comportamento mudou, testei aqui e funciona melhor ajustando "
            "o parametro X."
        )
        assert self.scorer.score({"text": texto}) > 0.5

    def test_pergunta_recebe_bonus(self):
        base = self.scorer.score({"text": "Isso funciona em todos os casos"})
        com_pergunta = self.scorer.score({"text": "Isso funciona em todos os casos?"})
        assert com_pergunta > base

    def test_autopromocao_score_zero(self):
        frases = [
            "Confira meu canal lá no instagram, tem conteúdo top!",
            "Segue lá, tem link na bio pra quem quiser conferir",
            "Gente, me segue lá que eu posto todo dia",
            "Clique no link da descrição pra saber mais",
        ]
        for frase in frases:
            assert self.scorer.score({"text": frase}) == 0.0

    def test_texto_legitimo_com_palavra_canal_nao_e_zerado(self):
        texto = "Gostei muito do canal de vocês, o conteúdo é sempre muito bem explicado"
        assert self.scorer.score({"text": texto}) > 0.0

    def test_score_sempre_em_faixa_valida(self):
        textos = ["", "kkk", "a" * 5000, "🎉🎉🎉 muito bom mesmo, adorei o video!"]
        for texto in textos:
            q = self.scorer.score({"text": texto})
            assert 0.0 <= q <= 1.0

    def test_score_batch_usa_score_individual(self):
        comentarios = [{"text": "kkk"}, {"text": "muito bom, aprendi bastante com esse video!"}]
        resultados = self.scorer.score_batch(comentarios)
        assert resultados == [self.scorer.score(c) for c in comentarios]

    def test_duplicata_longa_em_lote_score_zero(self):
        texto_longo = "Esse video mudou minha forma de pensar sobre o assunto"
        comentarios = [{"text": texto_longo}] * 3
        resultados = self.scorer.score_batch(comentarios)
        assert resultados == [0.0, 0.0, 0.0]

    def test_duplicata_curta_nao_e_zerada(self):
        comentarios = [{"text": "top demais"}] * 5
        resultados = self.scorer.score_batch(comentarios)
        individual = self.scorer.score({"text": "top demais"})
        assert individual > 0.0
        assert resultados == [individual] * 5

    def test_duplicata_abaixo_do_minimo_nao_e_zerada(self):
        texto_longo = "Esse video mudou minha forma de pensar sobre o assunto"
        comentarios = [{"text": texto_longo}] * 2
        resultados = self.scorer.score_batch(comentarios)
        individual = self.scorer.score({"text": texto_longo})
        assert resultados == [individual, individual]

    def test_duplicata_com_emoji_variando_ainda_e_detectada(self):
        # Emoji nao deve contar como caractere distintivo: o mesmo texto
        # longo com um emoji diferente no final ainda e a mesma duplicata.
        texto_longo = "Esse video mudou minha forma de pensar sobre o assunto"
        comentarios = [
            {"text": texto_longo + " 🎉"},
            {"text": texto_longo + " 🔥"},
            {"text": texto_longo + " 😀"},
        ]
        resultados = self.scorer.score_batch(comentarios)
        assert resultados == [0.0, 0.0, 0.0]

    def test_emoji_nao_conta_para_o_limiar_de_caracteres(self):
        # Texto curto com varios emoji nao deve ultrapassar DUP_MIN_CHARS
        # so por causa dos emoji (que sao removidos na normalizacao).
        texto_curto_com_emoji = "Gratidao pelos esclarecimentos.🙏🙏😊"
        comentarios = [{"text": texto_curto_com_emoji}] * 5
        resultados = self.scorer.score_batch(comentarios)
        individual = self.scorer.score({"text": texto_curto_com_emoji})
        assert resultados == [individual] * 5

    def test_score_batch_ainda_bate_com_score_individual_sem_duplicata(self):
        comentarios = [
            {"text": "Muito bom esse video, aprendi bastante!"},
            {"text": "Qual a fonte desse dado que voce citou?"},
            {"text": "kkkkk"},
        ]
        resultados = self.scorer.score_batch(comentarios)
        assert resultados == [self.scorer.score(c) for c in comentarios]

    def test_duplicata_acentuada_longa_em_lote_score_zero(self):
        # 42 chars normalizados preservando acentos; so 37 se os acentos forem
        # removidos (regressao: \w e ASCII-only no dtype str do pandas 3.x) --
        # esse texto so fica acima do DUP_MIN_CHARS=40 se a normalizacao
        # preservar corretamente os acentos.
        texto_longo = "Muito bom o vídeo, ótima explicação técnica"
        comentarios = [{"text": texto_longo}] * 3
        resultados = self.scorer.score_batch(comentarios)
        # Should detect as duplicate spam (3+ repetitions, >40 chars after normalization)
        assert resultados == [0.0, 0.0, 0.0]


class TestCategoryWeightedScorer:
    def setup_method(self):
        self.scorer = CategoryWeightedScorer()

    def test_sem_categorias_score_zero(self):
        assert self.scorer.score({"categorias": [], "score_engajamento": None}) == 0.0

    def test_contribuicao_valor_score_alto(self):
        q = self.scorer.score({"categorias": ["contribuicao_valor"], "score_engajamento": 0.9})
        assert q > 0.8

    def test_spam_score_zero(self):
        q = self.scorer.score({"categorias": ["spam_irrelevante"], "score_engajamento": 0.0})
        assert q == 0.0

    def test_multiplas_categorias_usa_a_de_maior_peso(self):
        q_so_agradecimento = self.scorer.score(
            {"categorias": ["agradecimento"], "score_engajamento": 0.2}
        )
        q_com_contribuicao = self.scorer.score(
            {"categorias": ["agradecimento", "contribuicao_valor"], "score_engajamento": 0.2}
        )
        assert q_com_contribuicao > q_so_agradecimento

    def test_score_sempre_em_faixa_valida(self):
        casos = [
            {"categorias": ["contribuicao_valor"], "score_engajamento": 1.0},
            {"categorias": ["spam_irrelevante"], "score_engajamento": 0.0},
            {"categorias": [], "score_engajamento": None},
            {"categorias": ["categoria_desconhecida"], "score_engajamento": 0.5},
        ]
        for caso in casos:
            q = self.scorer.score(caso)
            assert 0.0 <= q <= 1.0
