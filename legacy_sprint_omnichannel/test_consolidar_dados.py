"""Testes das funcoes puras de consolidar_dados.py."""

from datetime import datetime, timezone

import pandas as pd

from consolidar_dados import (
    truncar_title,
    calcular_engagement_total,
    calcular_hours_since_publish,
    normalizar_schema,
)


def _linha_bruta(**overrides):
    base = {
        "platform": "youtube",
        "content_id": "abc123",
        "title": "titulo de teste",
        "channel_title": "Canal Teste",
        "username": "",
        "subreddit": "",
        "created_at": "2026-08-08T12:00:00+00:00",
        "likes": 10,
        "comments": 5,
        "views": 100,
    }
    base.update(overrides)
    return base


class TestTruncarTitle:
    def test_texto_curto_nao_muda(self):
        assert truncar_title("titulo curto") == "titulo curto"

    def test_texto_longo_trunca_em_280(self):
        texto = "a" * 300
        resultado = truncar_title(texto)
        assert len(resultado) == 280

    def test_valor_nao_string_vira_string_vazia(self):
        assert truncar_title(float("nan")) == ""


class TestCalcularEngagementTotal:
    def test_formula_likes_mais_comments_vezes_dois(self):
        df = pd.DataFrame([{"likes": 10, "comments": 5}])
        resultado = calcular_engagement_total(df)
        assert resultado.loc[0, "engagement_total"] == 20

    def test_com_zero(self):
        df = pd.DataFrame([{"likes": 0, "comments": 0}])
        resultado = calcular_engagement_total(df)
        assert resultado.loc[0, "engagement_total"] == 0


class TestCalcularHoursSincePublish:
    def test_24_horas_atras(self):
        agora = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
        df = pd.DataFrame([{"created_at": "2026-08-08T12:00:00+00:00"}])
        resultado = calcular_hours_since_publish(df, agora=agora)
        assert resultado.loc[0, "hours_since_publish"] == 24.0

    def test_data_invalida_nao_quebra(self):
        agora = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
        df = pd.DataFrame([{"created_at": "data-invalida"}])
        resultado = calcular_hours_since_publish(df, agora=agora)
        assert pd.isna(resultado.loc[0, "hours_since_publish"]) or resultado.loc[0, "hours_since_publish"] >= 0


class TestNormalizarSchema:
    def test_colunas_finais_exatas(self):
        df = pd.DataFrame([_linha_bruta()])
        resultado = normalizar_schema(df)
        colunas_esperadas = {
            "platform", "content_id", "title", "channel_title", "username",
            "subreddit", "created_at", "likes", "comments", "views",
            "engagement_total", "hours_since_publish",
        }
        assert set(resultado.columns) == colunas_esperadas

    def test_nan_em_numericos_vira_zero(self):
        df = pd.DataFrame([_linha_bruta(likes=float("nan"), views=float("nan"))])
        resultado = normalizar_schema(df)
        assert resultado.loc[0, "likes"] == 0
        assert resultado.loc[0, "views"] == 0

    def test_numericos_sao_int(self):
        df = pd.DataFrame([_linha_bruta()])
        resultado = normalizar_schema(df)
        for col in ["likes", "comments", "views", "engagement_total"]:
            assert pd.api.types.is_integer_dtype(resultado[col])

    def test_dedup_por_content_id(self):
        df = pd.DataFrame([_linha_bruta(content_id="dup"), _linha_bruta(content_id="dup")])
        resultado = normalizar_schema(df)
        assert len(resultado) == 1

    def test_title_truncado(self):
        df = pd.DataFrame([_linha_bruta(title="a" * 300)])
        resultado = normalizar_schema(df)
        assert len(resultado.loc[0, "title"]) == 280
