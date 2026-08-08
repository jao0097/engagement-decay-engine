"""
Interface configuravel de scoring de qualidade textual.

Produz sempre um Q em [0, 1] a partir de um comentario, mas a forma de
calcular Q e plugavel: uma implementacao pode usar heuristicas locais (sem
LLM, para testes/dados sinteticos), outra pode reaproveitar a saida do
classificador Groq (classifier.py) sem precisar reprocessar texto.

O decay_engine so conhece a interface EngagementScorer; nao sabe (nem
precisa saber) qual implementacao concreta esta em uso.
"""

import re
from abc import ABC, abstractmethod

import pandas as pd

EMOJI_PATTERN = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF]+")


class EngagementScorer(ABC):
    """Interface de scoring: qualquer implementacao produz Q em [0, 1] por comentario."""

    @abstractmethod
    def score(self, comment: dict) -> float:
        """Recebe um comentario (dict com pelo menos 'text') e retorna Q em [0, 1]."""

    def score_batch(self, comments: list[dict]) -> list[float]:
        """Implementacao default: aplica score() a cada comentario da lista."""
        return [self.score(c) for c in comments]


class HeuristicScorer(EngagementScorer):
    """
    Scorer sem LLM, baseado em heuristicas de tamanho e estrutura do texto.

    Nao substitui a qualidade do classificador via Groq, mas roda offline e
    instantaneamente -- ideal para testes e para gerar dados sinteticos no
    dashboard quando o banco ainda nao esta povoado.
    """

    QUESTION_BONUS = 0.15
    LENGTH_CAP_CHARS = 240
    SELF_PROMO_PATTERNS = [
        re.compile(
            r"(confir[ae]|visit[ae]|segu[ae]|inscrev[ae]|assist[ae])"
            r".{0,25}(meu|nosso).{0,10}(canal|perfil|instagram|insta)",
            re.IGNORECASE,
        ),
        re.compile(r"link\s*na\s*bio", re.IGNORECASE),
        re.compile(r"me\s+segue", re.IGNORECASE),
        re.compile(r"(clique|acesse)\s+(no|o)\s+link", re.IGNORECASE),
    ]
    DUP_MIN_CHARS = 40
    DUP_MIN_COUNT = 3

    def score(self, comment: dict) -> float:
        text = (comment.get("text") or "").strip()
        if not text:
            return 0.0

        sem_emoji = EMOJI_PATTERN.sub("", text).strip()
        if len(sem_emoji) <= 2 or re.fullmatch(r"(k|h|s|rs|haha)+[\s!.]*", sem_emoji.lower()):
            return 0.0

        if any(padrao.search(text) for padrao in self.SELF_PROMO_PATTERNS):
            return 0.0

        comprimento_normalizado = min(len(sem_emoji), self.LENGTH_CAP_CHARS) / self.LENGTH_CAP_CHARS
        tem_pergunta = "?" in text
        palavras = sem_emoji.split()
        diversidade_lexical = min(len(set(w.lower() for w in palavras)) / max(len(palavras), 1), 1.0)

        score = 0.55 * comprimento_normalizado + 0.35 * diversidade_lexical
        if tem_pergunta:
            score += self.QUESTION_BONUS

        return max(0.0, min(score, 1.0))

    def score_batch(self, comments: list[dict]) -> list[float]:
        textos = pd.Series(
            [EMOJI_PATTERN.sub("", (c.get("text") or "")).strip().lower() for c in comments]
        )
        normalizados = (
            textos.str.replace(r"[^0-9a-zà-öø-ÿ\s]", "", regex=True)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
        )

        contagem = normalizados.value_counts()
        e_repetido = (normalizados.map(contagem) >= self.DUP_MIN_COUNT).to_numpy()
        e_longo = (normalizados.str.len() > self.DUP_MIN_CHARS).to_numpy()
        e_duplicata_spam = e_repetido & e_longo

        scores = [self.score(c) for c in comments]
        return [0.0 if dup else s for dup, s in zip(e_duplicata_spam, scores)]


class CategoryWeightedScorer(EngagementScorer):
    """
    Deriva Q combinando as categorias e o score_engajamento ja produzidos pelo
    pipeline Groq existente (classifier.py). Nao chama nenhum LLM: apenas
    reinterpreta uma classificacao ja feita sob a escala unica Q em [0, 1]
    usada pelo motor de decaimento.
    """

    CATEGORY_WEIGHTS = {
        "contribuicao_valor": 1.0,
        "critica_construtiva": 0.75,
        "pergunta_duvida": 0.55,
        "elogio_generico": 0.3,
        "agradecimento": 0.2,
        "critica_vazia": 0.1,
        "spam_irrelevante": 0.0,
        "sem_conteudo_classificavel": 0.0,
    }

    def score(self, comment: dict) -> float:
        categorias = comment.get("categorias") or []
        score_engajamento = comment.get("score_engajamento")

        pesos = [self.CATEGORY_WEIGHTS.get(c, 0.0) for c in categorias]
        base = max(pesos) if pesos else 0.0

        if score_engajamento is None:
            return max(0.0, min(base, 1.0))

        combinado = (base + float(score_engajamento)) / 2.0
        return max(0.0, min(combinado, 1.0))
