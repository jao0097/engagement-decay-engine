"""
Dashboard MVP: saude da comunidade sob o modelo de decaimento de energia.

Uso:
    streamlit run app.py

Se ./engagement.db ja tiver estado calculado (author_engagement_state
povoada pelo job diario), o dashboard le direto de la. Caso contrario,
gera um dataset sintetico na hora (arquetipos de autor casual/regular/
super-fa ao longo de 150 dias) e roda o motor de decaimento sobre ele, para
que o app funcione instantaneamente sem nenhuma dependencia externa.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import decay_engine
from scoring_engine import HeuristicScorer

LEVEL_LABELS = {
    1: "L1 - Novato",
    2: "L2 - Casual",
    3: "L3 - Engajado",
    4: "L4 - Fa",
    5: "L5 - Super-fa",
}

MOCK_TEXT_TEMPLATES = {
    "casual": [
        "kkkkk",
        "muito bom",
        "top demais",
        "gostei",
        "valeu pelo video",
    ],
    "regular": [
        "Muito bom esse video, aprendi bastante sobre o assunto!",
        "Tenho uma duvida: isso funciona pra qualquer caso ou so pra esse exemplo?",
        "Excelente explicacao, ficou bem clara a parte do meio.",
        "Acho que faltou explicar melhor a segunda parte, mas no geral gostei.",
    ],
    "super_fan": [
        "Cara, esse e o melhor video que voce ja fez sobre esse assunto, muito completo!",
        "Vou complementar: na versao mais recente esse comportamento mudou um pouco, "
        "vale testar antes de aplicar em producao.",
        "Excelente conteudo como sempre! Testei aqui e realmente funciona, "
        "so precisei ajustar um parametro.",
        "Sugestao de melhoria: seria otimo um video comparando essa abordagem "
        "com a alternativa mais tradicional, acho que ajudaria muita gente.",
    ],
}


def generate_mock_events(
    n_authors: int = 400, days: int = 150, seed: int = 42
) -> pd.DataFrame:
    """Gera um dataset sintetico de eventos de engajamento, com tres arquetipos de autor."""
    rng = np.random.default_rng(seed)
    scorer = HeuristicScorer()
    now = pd.Timestamp.now(tz="UTC").normalize()
    start = now - pd.Timedelta(days=days)

    archetypes = rng.choice(
        ["casual", "regular", "super_fan"], size=n_authors, p=[0.6, 0.3, 0.1]
    )

    rows = []
    event_seq = 0
    for i, archetype in enumerate(archetypes):
        author_id = f"UC_mock_{i:04d}"
        author_name = f"Autor {i:04d}"

        if archetype == "casual":
            n_events = rng.integers(1, 4)
            active_window = days
        elif archetype == "regular":
            n_events = rng.integers(5, 20)
            active_window = days
        else:  # super_fan: metade continua ativo, metade "sumiu" ha algumas semanas
            n_events = rng.integers(25, 60)
            ficou_quieto = rng.random() < 0.5
            active_window = rng.integers(30, 60) if ficou_quieto else days

        offsets = rng.integers(0, max(active_window, 1), size=n_events)
        offsets.sort()

        for offset in offsets:
            published_at = start + pd.Timedelta(days=int(offset), hours=int(rng.integers(0, 24)))
            text = rng.choice(MOCK_TEXT_TEMPLATES[archetype])
            quality_score = scorer.score({"text": text})

            rows.append(
                {
                    "event_id": f"evt_{event_seq}",
                    "comment_id": f"cmt_{event_seq}",
                    "author_channel_id": author_id,
                    "author_display_name": author_name,
                    "video_id": f"vid_{rng.integers(0, 30):03d}",
                    "published_at": published_at,
                    "quality_score": float(quality_score),
                    "categorias": "",
                }
            )
            event_seq += 1

    return pd.DataFrame(rows)


@st.cache_data(show_spinner="Carregando dados de engajamento...")
def load_dashboard_data(base_weight: float):
    db_path = Path(decay_engine.DB_PATH)
    if db_path.exists():
        conn = decay_engine.get_connection(str(db_path))
        state = decay_engine.load_state(conn)
        conn.close()
        if not state.empty:
            return state, None, "banco de dados (engagement.db)"

    events = generate_mock_events()
    state, history = decay_engine.backfill_history(
        events, base_weight=base_weight, keep_history=True
    )
    return state, history, "dados sinteticos (mock, gerados nesta sessao)"


def render_metrics(state: pd.DataFrame, at_risco: pd.DataFrame) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Autores rastreados", f"{len(state):,}".replace(",", "."))
    col2.metric("Energia media", f"{state['energy'].mean():.1f}")
    pct_l4_l5 = 100 * (state["level"] >= 4).mean() if len(state) else 0.0
    col3.metric("% em L4/L5", f"{pct_l4_l5:.1f}%")
    col4.metric("Super-fas em risco", len(at_risco))


def render_level_distribution(state: pd.DataFrame) -> None:
    contagem = (
        state["level"]
        .map(LEVEL_LABELS)
        .value_counts()
        .reindex(LEVEL_LABELS.values(), fill_value=0)
        .reset_index()
    )
    contagem.columns = ["nivel", "autores"]
    fig = px.bar(contagem, x="nivel", y="autores", title="Distribuicao de autores por nivel")
    st.plotly_chart(fig, use_container_width=True)


def render_energy_over_time(history: pd.DataFrame | None) -> None:
    if history is None or history.empty:
        st.info(
            "Curva temporal disponivel apenas quando o estado e reconstruido a partir "
            "do historico de eventos (dados sinteticos ou replay completo)."
        )
        return

    media_diaria = (
        history.groupby(["date", "level"])["energy"].mean().reset_index()
    )
    media_diaria["nivel"] = media_diaria["level"].map(LEVEL_LABELS)

    fig = go.Figure()
    for nivel in LEVEL_LABELS.values():
        serie = media_diaria[media_diaria["nivel"] == nivel]
        if serie.empty:
            continue
        fig.add_trace(go.Scatter(x=serie["date"], y=serie["energy"], mode="lines", name=nivel))
    fig.update_layout(title="Energia media por nivel ao longo do tempo", xaxis_title="Data",
                       yaxis_title="Energia")
    st.plotly_chart(fig, use_container_width=True)


def render_risk_table(at_risco: pd.DataFrame) -> None:
    st.subheader("Super-fas (L4/L5) em risco iminente de evasao")
    if at_risco.empty:
        st.success("Nenhum super-fa em risco no momento, dado o buffer selecionado.")
        return

    tabela = at_risco.reset_index()[
        ["author_channel_id", "level", "projected_level", "projected_energy",
         "distance_to_demotion", "last_event_at"]
    ].copy()
    tabela["level"] = tabela["level"].map(LEVEL_LABELS)
    tabela["projected_level"] = tabela["projected_level"].map(LEVEL_LABELS)
    tabela.columns = ["Autor", "Nivel (ultimo calculo)", "Nivel projetado hoje", "Energia projetada",
                       "Distancia p/ queda de nivel",
                       "Ultimo evento"]
    st.dataframe(tabela, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Engajamento - Decaimento", layout="wide")
    st.title("Saude da comunidade: energia de engajamento com decaimento")
    st.caption(
        "Modelo N(t) = N0 * e^(-lambda * dt), niveis L1-L5 com meia-vida progressiva "
        "e histerese de 3 pontos na queda de nivel."
    )

    with st.sidebar:
        st.header("Parametros")
        base_weight = st.slider("Peso base por evento (delta_E = Q x peso)", 5.0, 50.0, 20.0, 1.0)
        buffer = st.slider("Buffer de risco (pontos ate a queda de nivel)", 1.0, 30.0, 10.0, 1.0)

    state, history, fonte = load_dashboard_data(base_weight)
    st.caption(f"Fonte dos dados: {fonte}")

    if state.empty:
        st.warning("Nenhum autor encontrado.")
        return

    at_risco = decay_engine.churn_risk_report(state, buffer=buffer)

    render_metrics(state, at_risco)
    render_level_distribution(state)
    render_energy_over_time(history)
    render_risk_table(at_risco)

    with st.expander("Estado bruto por autor"):
        st.dataframe(state.reset_index(), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
