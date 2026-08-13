"""Testes de build_engagement_state.py. Rodar com: pytest test_build_engagement_state.py -v"""

import pandas as pd

import decay_engine as de
from build_engagement_state import get_events_cutoff


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
