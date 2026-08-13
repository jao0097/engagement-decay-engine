"""Testes de build_engagement_state.py. Rodar com: pytest test_build_engagement_state.py -v"""

import json

import pandas as pd
import pytest

import decay_engine as de
from build_engagement_state import (
    get_events_cutoff,
    load_and_score_comments,
    load_whatsapp_events,
    process_platform,
    to_decay_engine_events,
    youtube_to_universal_events,
)


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


class TestLoadAndScoreComments:
    def test_sem_arquivos_retorna_dataframe_vazio_com_colunas(self, tmp_path):
        raw_path = tmp_path / "comentarios_brutos.json"
        classified_path = tmp_path / "comentarios_classificados.json"

        resultado = load_and_score_comments(str(raw_path), str(classified_path))

        assert isinstance(resultado, pd.DataFrame)
        assert list(resultado.columns) == [
            "comment_id", "author", "video_id", "published_at", "quality_score", "categorias",
        ]
        assert len(resultado) == 0


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


class TestProcessPlatform:
    def test_grava_eventos_e_estado_para_uma_plataforma(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)

        universal = pd.DataFrame(
            [
                _evento_universal_wa(author_id="joao", published_at="2026-08-01T10:00:00+00:00"),
                _evento_universal_wa(author_id="joao", published_at="2026-08-02T10:00:00+00:00", event_id="e2"),
            ]
        )
        process_platform(conn, "whatsapp", universal, base_weight=20.0, banco_existente=False)

        estado = de.load_state(conn)
        eventos_gravados = conn.execute("SELECT COUNT(*) FROM engagement_events").fetchone()[0]
        conn.close()

        assert "whatsapp:joao" in estado.index
        assert eventos_gravados == 2

    def test_plataforma_sem_eventos_nao_quebra(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)

        vazio = pd.DataFrame(columns=[
            "event_id", "platform", "author_id", "author_display_name",
            "content_id", "published_at", "quality_score", "categorias",
        ])
        process_platform(conn, "whatsapp", vazio, base_weight=20.0, banco_existente=False)
        conn.close()  # nao deve levantar excecao

    def test_segunda_plataforma_nao_apaga_estado_da_primeira(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)

        yt = pd.DataFrame([_evento_universal(event_id="y1", platform="youtube", author_id="ana")])
        wa = pd.DataFrame([_evento_universal_wa(author_id="joao")])

        process_platform(conn, "youtube", yt, base_weight=20.0, banco_existente=False)
        process_platform(conn, "whatsapp", wa, base_weight=20.0, banco_existente=True)

        estado = de.load_state(conn)
        conn.close()

        assert "youtube:ana" in estado.index
        assert "whatsapp:joao" in estado.index

    def test_incremental_reincluir_mesmo_minuto_sem_perder_nem_duplicar(self, tmp_path):
        """Cobre Issue 2: uma mensagem NOVA que cai no MESMO MINUTO do cutoff
        anterior nao pode ser perdida (bug do '>' estrito no corte
        incremental), e uma mensagem ja persistida nesse minuto nao pode ser
        reaplicada -- a dedup precisa ser por event_id, nao so por timestamp."""
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)

        primeira_leva = pd.DataFrame(
            [
                _evento_universal_wa(author_id="joao", event_id="e1", published_at="2026-08-01T10:00:00+00:00"),
                _evento_universal_wa(author_id="joao", event_id="e2", published_at="2026-08-01T10:05:00+00:00"),
            ]
        )
        process_platform(conn, "whatsapp", primeira_leva, base_weight=20.0, banco_existente=False)

        segunda_leva = pd.DataFrame(
            [
                # ja persistido na primeira leva -- nao pode ser reaplicado.
                _evento_universal_wa(author_id="joao", event_id="e2", published_at="2026-08-01T10:05:00+00:00"),
                # novo, mas cai no MESMO minuto do cutoff (max(published_at) da primeira leva)
                # -- com filtro '>' estrito, seria perdido para sempre.
                _evento_universal_wa(author_id="joao", event_id="e2b", published_at="2026-08-01T10:05:00+00:00"),
                # genuinamente novo, minuto seguinte.
                _evento_universal_wa(author_id="joao", event_id="e3", published_at="2026-08-01T10:10:00+00:00"),
            ]
        )
        process_platform(conn, "whatsapp", segunda_leva, base_weight=20.0, banco_existente=True)

        eventos_gravados = conn.execute("SELECT COUNT(*) FROM engagement_events").fetchone()[0]
        ids_gravados = {r[0] for r in conn.execute("SELECT event_id FROM engagement_events").fetchall()}
        estado_incremental = de.load_state(conn)
        conn.close()

        assert eventos_gravados == 4
        assert ids_gravados == {"e1", "e2", "e2b", "e3"}

        # comparado a um processamento unico de todos os 4 eventos (mesmo dia,
        # entao sem decaimento entre as levas), a energia final deve bater --
        # nem perdeu e2b (bug do '>'), nem duplicou e2 (dedup por event_id).
        db_path_completo = tmp_path / "completo.db"
        conn2 = de.get_connection(str(db_path_completo))
        de.init_schema(conn2)
        todos = pd.DataFrame(
            [
                _evento_universal_wa(author_id="joao", event_id="e1", published_at="2026-08-01T10:00:00+00:00"),
                _evento_universal_wa(author_id="joao", event_id="e2", published_at="2026-08-01T10:05:00+00:00"),
                _evento_universal_wa(author_id="joao", event_id="e2b", published_at="2026-08-01T10:05:00+00:00"),
                _evento_universal_wa(author_id="joao", event_id="e3", published_at="2026-08-01T10:10:00+00:00"),
            ]
        )
        process_platform(conn2, "whatsapp", todos, base_weight=20.0, banco_existente=False)
        estado_completo = de.load_state(conn2)
        conn2.close()

        assert estado_incremental.loc["whatsapp:joao", "energy"] == pytest.approx(
            estado_completo.loc["whatsapp:joao", "energy"]
        )


def _evento_universal_wa(author_id="joao", published_at="2026-08-01T10:00:00+00:00", event_id="e1"):
    return _evento_universal(
        event_id=event_id, platform="whatsapp", author_id=author_id,
        content_id="Grupo X", published_at=published_at,
    )


class TestLoadWhatsappEvents:
    def test_arquivo_inexistente_retorna_dataframe_vazio_com_colunas(self, tmp_path):
        path = tmp_path / "nao_existe.json"
        resultado = load_whatsapp_events(str(path))
        assert isinstance(resultado, pd.DataFrame)
        assert resultado.empty
        assert list(resultado.columns) == [
            "event_id", "platform", "author_id", "author_display_name",
            "content_id", "published_at", "quality_score", "categorias",
        ]

    def test_arquivo_json_vazio_retorna_dataframe_vazio(self, tmp_path):
        path = tmp_path / "vazio.json"
        path.write_text("[]", encoding="utf-8")
        resultado = load_whatsapp_events(str(path))
        assert resultado.empty

    def test_carrega_eventos_do_json(self, tmp_path):
        path = tmp_path / "eventos.json"
        path.write_text(json.dumps([_evento_universal_wa(event_id="e1")]), encoding="utf-8")
        resultado = load_whatsapp_events(str(path))
        assert len(resultado) == 1
        assert resultado.iloc[0]["platform"] == "whatsapp"
        assert resultado.iloc[0]["event_id"] == "e1"
