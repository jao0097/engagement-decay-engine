"""Testes das funcoes puras de analise_exploratoria.py."""

import pandas as pd

from analise_exploratoria import bucketizar_tempo, calcular_estatisticas_por_plataforma


class TestBucketizarTempo:
    def test_janelas_corretas(self):
        df = pd.DataFrame(
            {
                "hours_since_publish": [1.0, 23.9, 24.1, 100.0, 168.0, 168.1, 500.0],
            }
        )
        resultado = bucketizar_tempo(df)
        assert list(resultado["janela_temporal"]) == [
            "0-24h",
            "0-24h",
            "1-7 dias",
            "1-7 dias",
            "1-7 dias",
            "7+ dias",
            "7+ dias",
        ]


class TestCalcularEstatisticasPorPlataforma:
    def test_media_mediana_desvio_por_plataforma(self):
        df = pd.DataFrame(
            {
                "platform": ["youtube", "youtube", "reddit"],
                "likes": [10, 20, 5],
                "comments": [1, 2, 1],
                "views": [100, 200, 0],
                "engagement_total": [12, 24, 7],
            }
        )
        resultado = calcular_estatisticas_por_plataforma(df)
        assert "youtube" in resultado.index
        assert "reddit" in resultado.index
        assert resultado.loc["youtube", ("likes", "mean")] == 15.0
