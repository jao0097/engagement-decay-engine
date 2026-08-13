"""Testes de build_engagement_state.py. Rodar com: pytest test_build_engagement_state.py -v"""

import pandas as pd

import decay_engine as de
from build_engagement_state import get_events_cutoff, youtube_to_universal_events, to_decay_engine_events


def _eventos_duas_plataformas():
    return pd.DataFrame(
        [
            {"event_id": "e1", "event_source_id": "e1", "platform": "youtube",
             "author_channel_id": "youtube:a", "author_display_name": "a",
             "content_id": "v1", "published_at": "2026-08-01T10:00:00+00:00",
             "quality_score": 0.5, "categorias": ""},
            {"event_id": "e2", "event_source_id": "e2", "platform": "whatsapp",
             "author_channel_id": "whatsapp:a", "author_display_name": "a",
             "content_id": "g1", "published_at": "2026-08-05T10:00:00+00:00",
             "quality_score": 0.5, "categorias": ""},
        ]
    )


class TestYoutubeToUniversalEvents:
    def test_mapeia_colunas_e_fixa_platform(self):
        df = pd.DataFrame(
            [
                {
                    "comment_id": "c1",
                    "author": "@fulano",
                    "video_id": "v1",
                    "published_at": "2026-08-01T10:00:00+00:00",
                    "quality_score": 0.7,
                    "categorias": ["elogio_generico"],
                }
            ]
        )
        resultado = youtube_to_universal_events(df)
        assert resultado.iloc[0]["platform"] == "youtube"
        assert resultado.iloc[0]["author_id"] == "@fulano"
        assert resultado.iloc[0]["content_id"] == "v1"
        assert resultado.iloc[0]["categorias"] == "elogio_generico"
        assert resultado.iloc[0]["event_id"] == "c1"

    def test_sem_categorias_vira_string_vazia(self):
        df = pd.DataFrame(
            [{"comment_id": "c1", "author": "@a", "video_id": "v1",
              "published_at": "2026-08-01T10:00:00+00:00", "quality_score": 0.5}]
        )
        resultado = youtube_to_universal_events(df)
        assert resultado.iloc[0]["categorias"] == ""

    def test_dedup_por_event_id(self):
        df = pd.DataFrame(
            [
                {"comment_id": "dup", "author": "@a", "video_id": "v1",
                 "published_at": "2026-08-01T10:00:00+00:00", "quality_score": 0.5, "categorias": []},
                {"comment_id": "dup", "author": "@a", "video_id": "v1",
                 "published_at": "2026-08-01T10:00:00+00:00", "quality_score": 0.5, "categorias": []},
            ]
        )
        resultado = youtube_to_universal_events(df)
        assert len(resultado) == 1


class TestGetEventsCutoff:
    def test_filtra_por_plataforma(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)
        de.insert_events(conn, _eventos_duas_plataformas())

        cutoff_youtube = get_events_cutoff(conn, "youtube")
        cutoff_whatsapp = get_events_cutoff(conn, "whatsapp")
        conn.close()

        assert cutoff_youtube == pd.Timestamp("2026-08-01T10:00:00+00:00")
        assert cutoff_whatsapp == pd.Timestamp("2026-08-05T10:00:00+00:00")

    def test_sem_eventos_retorna_none(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)
        resultado = get_events_cutoff(conn, "youtube")
        conn.close()
        assert resultado is None


def _evento_universal(**overrides):
    base = {
        "event_id": "e1", "platform": "youtube", "author_id": "joao",
        "author_display_name": "joao", "content_id": "v1",
        "published_at": "2026-08-01T10:00:00+00:00", "quality_score": 0.5, "categorias": "",
    }
    base.update(overrides)
    return base


class TestToDecayEngineEvents:
    def test_namespacing_evita_colisao_entre_plataformas(self):
        universal = pd.DataFrame(
            [
                _evento_universal(event_id="e1", platform="youtube"),
                _evento_universal(event_id="e2", platform="whatsapp", content_id="Grupo X"),
            ]
        )
        resultado = to_decay_engine_events(universal)
        assert set(resultado["author_channel_id"]) == {"youtube:joao", "whatsapp:joao"}

    def test_event_source_id_copia_event_id(self):
        universal = pd.DataFrame([_evento_universal(event_id="e1")])
        resultado = to_decay_engine_events(universal)
        assert resultado.iloc[0]["event_source_id"] == "e1"
