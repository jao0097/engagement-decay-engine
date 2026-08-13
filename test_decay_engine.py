"""
Testes do motor de decaimento: matematica de decaimento/histerese e o
replay diario em lote. Rodar com: pytest test_decay_engine.py -v
"""

import numpy as np
import pandas as pd
import pytest

import decay_engine as de


def test_schema_tem_colunas_de_plataforma(tmp_path):
    db_path = tmp_path / "test.db"
    conn = de.get_connection(str(db_path))
    de.init_schema(conn)

    colunas_authors = {row[1] for row in conn.execute("PRAGMA table_info(authors)").fetchall()}
    colunas_events = {row[1] for row in conn.execute("PRAGMA table_info(engagement_events)").fetchall()}
    conn.close()

    assert "platform" in colunas_authors
    assert {"platform", "content_id", "event_source_id"} <= colunas_events
    assert "video_id" not in colunas_events
    assert "comment_id" not in colunas_events


# --------------------------------------------------------------------------
# meia-vida e decaimento
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "energia, meia_vida_esperada",
    [(0, 7.0), (15, 7.0), (35, 7.0), (35.01, 15.0), (60, 15.0), (60.01, 30.0), (100, 30.0)],
)
def test_half_life_buckets(energia, meia_vida_esperada):
    assert de.half_life_days(np.array([energia]))[0] == meia_vida_esperada


def test_decay_meia_vida_reduz_energia_pela_metade():
    # autor em L4/L5 (t1/2=30d): apos exatamente 30 dias, energia cai a metade.
    resultado = de.decay(np.array([100.0]), np.array([30.0]))
    assert resultado[0] == pytest.approx(50.0, abs=0.01)


def test_decay_delta_negativo_e_tratado_como_zero():
    resultado = de.decay(np.array([80.0]), np.array([-5.0]))
    assert resultado[0] == pytest.approx(80.0)


def test_decay_zero_permanece_zero():
    resultado = de.decay(np.array([0.0]), np.array([9999.0]))
    assert resultado[0] == 0.0


def test_apply_energy_gain_satura_em_100():
    resultado = de.apply_energy_gain(np.array([90.0]), np.array([50.0]))
    assert resultado[0] == 100.0


def test_apply_energy_gain_nao_fica_negativo():
    resultado = de.apply_energy_gain(np.array([0.0]), np.array([0.0]))
    assert resultado[0] == 0.0


# --------------------------------------------------------------------------
# histerese
# --------------------------------------------------------------------------

def test_hysteresis_sobida_e_estrita_no_limiar():
    # exatamente no limiar (60) ja conta como L4.
    novo = de.apply_hysteresis(np.array([3]), np.array([60.0]))
    assert novo[0] == 4


def test_hysteresis_zona_morta_nao_derruba_nivel():
    # 58 esta abaixo de 60 mas acima do limiar de queda (57) -> mantem L4.
    novo = de.apply_hysteresis(np.array([4]), np.array([58.0]))
    assert novo[0] == 4


def test_hysteresis_queda_real_exige_passar_da_margem():
    # 55 esta abaixo do limiar de queda (57) -> cai para L3.
    novo = de.apply_hysteresis(np.array([4]), np.array([55.0]))
    assert novo[0] == 3


def test_hysteresis_pode_pular_varios_niveis_de_uma_vez():
    novo = de.apply_hysteresis(np.array([1]), np.array([90.0]))
    assert novo[0] == 5


def test_hysteresis_vetorizada_processa_varios_autores_independentemente():
    prior = np.array([1, 4, 4])
    energia = np.array([90.0, 58.0, 55.0])
    novo = de.apply_hysteresis(prior, energia)
    assert list(novo) == [5, 4, 3]


# --------------------------------------------------------------------------
# lote diario / backfill
# --------------------------------------------------------------------------

def _eventos_dois_autores():
    return pd.DataFrame(
        [
            {"author_channel_id": "A", "published_at": "2026-01-01T10:00:00Z", "quality_score": 1.0},
            {"author_channel_id": "A", "published_at": "2026-01-02T10:00:00Z", "quality_score": 1.0},
            {"author_channel_id": "B", "published_at": "2026-01-01T10:00:00Z", "quality_score": 0.1},
        ]
    )


def test_backfill_history_autor_mais_ativo_termina_com_mais_energia():
    state, _ = de.backfill_history(_eventos_dois_autores(), base_weight=20.0)
    assert state.loc["A", "energy"] > state.loc["B", "energy"]


def test_backfill_history_keep_history_produz_snapshots_por_dia():
    state, history = de.backfill_history(_eventos_dois_autores(), base_weight=20.0, keep_history=True)
    assert history is not None
    assert set(history["date"].dt.normalize().unique()) == {
        pd.Timestamp("2026-01-01", tz="UTC"),
        pd.Timestamp("2026-01-02", tz="UTC"),
    }


def test_backfill_history_pula_dias_sem_evento_sem_alterar_resultado():
    # mesmo evento, mas com um gap de dias vazios no meio -- so importa o
    # delta de tempo entre eventos reais, nao quantos dias existem no meio.
    eventos_com_gap = pd.DataFrame(
        [
            {"author_channel_id": "A", "published_at": "2026-01-01T00:00:00Z", "quality_score": 1.0},
            {"author_channel_id": "A", "published_at": "2026-03-01T00:00:00Z", "quality_score": 1.0},
        ]
    )
    state, _ = de.backfill_history(eventos_com_gap, base_weight=20.0)
    assert state.loc["A", "energy"] > 0


def test_backfill_history_incremental_continua_do_estado_existente():
    # dia 1: autor A comenta, gerando um primeiro estado persistido.
    eventos_dia1 = pd.DataFrame(
        [{"author_channel_id": "A", "published_at": "2026-01-01T00:00:00Z", "quality_score": 1.0}]
    )
    estado_inicial, _ = de.backfill_history(eventos_dia1, base_weight=20.0)

    # dia 2: reprocessamento incremental so com o evento NOVO, a partir do estado salvo.
    eventos_dia2 = pd.DataFrame(
        [{"author_channel_id": "A", "published_at": "2026-01-02T00:00:00Z", "quality_score": 1.0}]
    )
    estado_incremental, _ = de.backfill_history(
        eventos_dia2, base_weight=20.0, initial_state=estado_inicial
    )

    # reprocessamento completo (dia1+dia2 de uma vez) deve dar o mesmo resultado final.
    eventos_completos = pd.concat([eventos_dia1, eventos_dia2], ignore_index=True)
    estado_completo, _ = de.backfill_history(eventos_completos, base_weight=20.0)

    assert estado_incremental.loc["A", "energy"] == pytest.approx(estado_completo.loc["A", "energy"])


def test_backfill_history_incremental_inicializa_autor_novo():
    estado_existente = de.init_state_for_authors(["A"], as_of=pd.Timestamp("2026-01-01", tz="UTC"))
    eventos_novos = pd.DataFrame(
        [{"author_channel_id": "B", "published_at": "2026-01-05T00:00:00Z", "quality_score": 1.0}]
    )
    novo_estado, _ = de.backfill_history(eventos_novos, base_weight=20.0, initial_state=estado_existente)
    assert "A" in novo_estado.index
    assert "B" in novo_estado.index
    assert novo_estado.loc["B", "energy"] > 0


def test_run_daily_batch_autor_sem_evento_apenas_decai():
    # estado consistente: energia 50 corresponde a L3 (35 < 50 <= 60).
    state = de.init_state_for_authors(["A"], as_of=pd.Timestamp("2026-01-01", tz="UTC"))
    state["energy"] = 50.0
    state["level"] = 3
    dia_vazio = pd.DataFrame(columns=["author_channel_id", "quality_score"])
    novo_estado = de.run_daily_batch(state, dia_vazio, as_of=pd.Timestamp("2026-01-08", tz="UTC"))
    assert novo_estado.loc["A", "energy"] < 50.0
    assert novo_estado.loc["A", "level"] <= 3  # sem evento novo, nivel nunca sobe


# --------------------------------------------------------------------------
# churn_risk_report
# --------------------------------------------------------------------------

def test_churn_risk_report_ignora_autores_abaixo_de_l4():
    state = pd.DataFrame(
        {
            "energy": [50.0],
            "level": [3],
            "last_update_at": [pd.Timestamp("2026-01-01", tz="UTC")],
            "last_event_at": [pd.Timestamp("2026-01-01", tz="UTC")],
        },
        index=pd.Index(["A"], name="author_channel_id"),
    )
    risco = de.churn_risk_report(state, buffer=100.0, as_of=pd.Timestamp("2026-01-01", tz="UTC"))
    assert risco.empty


def test_churn_risk_report_calcula_projected_level_nao_so_energia():
    # autor persistido como L5 (energia 100 na ultima medicao), mas ja se
    # passaram 60 dias (2 meias-vidas de 30d) sem novo evento: energia real
    # projetada cai a 25, que e L2 -- o relatorio deve refletir isso em
    # projected_level, mesmo que o campo `level` persistido ainda diga 5.
    state = pd.DataFrame(
        {
            "energy": [100.0],
            "level": [5],
            "last_update_at": [pd.Timestamp("2026-01-01", tz="UTC")],
            "last_event_at": [pd.Timestamp("2026-01-01", tz="UTC")],
        },
        index=pd.Index(["A"], name="author_channel_id"),
    )
    risco = de.churn_risk_report(state, buffer=100.0, as_of=pd.Timestamp("2026-03-02", tz="UTC"))
    assert risco.loc["A", "level"] == 5
    assert risco.loc["A", "projected_level"] == 2
    assert risco.loc["A", "projected_energy"] == pytest.approx(25.0, abs=0.5)


# --------------------------------------------------------------------------
# persistencia SQLite (round-trip)
# --------------------------------------------------------------------------

def test_persistencia_sqlite_round_trip(tmp_path):
    db_path = tmp_path / "test.db"
    conn = de.get_connection(str(db_path))
    de.init_schema(conn)

    eventos = _eventos_dois_autores().copy()
    eventos["author_display_name"] = eventos["author_channel_id"]
    eventos["event_id"] = [f"e{i}" for i in range(len(eventos))]
    eventos["event_source_id"] = eventos["event_id"]
    eventos["platform"] = "youtube"
    eventos["content_id"] = "v1"
    eventos["categorias"] = ""
    de.insert_events(conn, eventos)

    state, _ = de.backfill_history(_eventos_dois_autores(), base_weight=20.0)
    de.save_state(conn, state)

    recarregado = de.load_state(conn)
    conn.close()

    assert recarregado.loc["A", "energy"] == pytest.approx(state.loc["A", "energy"])
    assert recarregado.loc["A", "level"] == state.loc["A", "level"]


def test_insert_events_persiste_platform(tmp_path):
    db_path = tmp_path / "test.db"
    conn = de.get_connection(str(db_path))
    de.init_schema(conn)

    eventos = _eventos_dois_autores().copy()
    eventos["author_display_name"] = eventos["author_channel_id"]
    eventos["event_id"] = [f"e{i}" for i in range(len(eventos))]
    eventos["event_source_id"] = eventos["event_id"]
    eventos["platform"] = "whatsapp"
    eventos["content_id"] = "Grupo Teste"
    eventos["categorias"] = ""
    de.insert_events(conn, eventos)

    linhas = conn.execute("SELECT DISTINCT platform FROM engagement_events").fetchall()
    conn.close()

    assert linhas == [("whatsapp",)]


# --------------------------------------------------------------------------
# cross-platform (namespacing de author_channel_id)
# --------------------------------------------------------------------------

def test_duas_plataformas_com_mesmo_author_id_nao_colidem():
    eventos_youtube = pd.DataFrame(
        [{"author_channel_id": "youtube:joao", "published_at": "2026-01-01T10:00:00Z", "quality_score": 1.0}]
    )
    eventos_whatsapp = pd.DataFrame(
        [{"author_channel_id": "whatsapp:joao", "published_at": "2026-01-01T10:00:00Z", "quality_score": 1.0}]
    )

    estado_youtube, _ = de.backfill_history(eventos_youtube, base_weight=20.0)
    estado_whatsapp, _ = de.backfill_history(eventos_whatsapp, base_weight=5.0)

    estado_final = pd.concat([estado_youtube, estado_whatsapp])

    assert set(estado_final.index) == {"youtube:joao", "whatsapp:joao"}
    assert estado_final.loc["youtube:joao", "energy"] != estado_final.loc["whatsapp:joao", "energy"]


def test_churn_risk_report_funciona_com_autores_de_plataformas_diferentes():
    agora = pd.Timestamp.now(tz="UTC")
    estado = pd.DataFrame(
        {
            "energy": [65.0, 65.0],
            "level": [4, 4],
            "last_update_at": [agora, agora],
            "last_event_at": [agora, agora],
            "updated_at": [agora, agora],
        },
        index=pd.Index(["youtube:joao", "whatsapp:joao"], name="author_channel_id"),
    )
    risco = de.churn_risk_report(estado, buffer=50.0)
    assert set(risco.index) == {"youtube:joao", "whatsapp:joao"}
