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

    def test_score_sempre_em_faixa_valida(self):
        textos = ["", "kkk", "a" * 5000, "🎉🎉🎉 muito bom mesmo, adorei o video!"]
        for texto in textos:
            q = self.scorer.score({"text": texto})
            assert 0.0 <= q <= 1.0

    def test_score_batch_usa_score_individual(self):
        comentarios = [{"text": "kkk"}, {"text": "muito bom, aprendi bastante com esse video!"}]
        resultados = self.scorer.score_batch(comentarios)
        assert resultados == [self.scorer.score(c) for c in comentarios]


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
