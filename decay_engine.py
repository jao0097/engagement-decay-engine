"""
Motor de decaimento de energia de engajamento.

Modelo fisico: N(t) = N0 * e^(-lambda * dt), com lambda = ln(2) / t_1/2 e
t_1/2 dependente do nivel psicologico atual do autor (L1..L5). A cada novo
evento de engajamento, a energia decaida recebe um reforco delta_E = Q * peso_base
(Q em [0, 1], vindo de um EngagementScorer) e e limitada a 100.

Nivel (L1..L5) e recalculado a cada atualizacao com histerese: subida de
nivel e imediata ao cruzar o limiar superior; queda de nivel so acontece
quando a energia cai abaixo de (limiar_inferior - 3), para nao ficar
oscilando na borda.

Escala e memoria: o calculo NUNCA itera linha a linha sobre o dataset de
comentarios (que pode ter milhoes de linhas). O estado por autor e um
DataFrame com uma linha por autor (milhares, nao milhoes), atualizado em
lote via numpy/pandas vetorizado. Para reprocessar historico, o unico laco
em Python puro percorre DIAS DO CALENDARIO (dezenas a milhares de
iteracoes) -- nunca comentarios -- e cada iteracao le do SQLite so a janela
daquele dia (indice em published_at), mantendo o uso de memoria limitado a
"um dia de eventos" por vez, nao ao dataset inteiro.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DB_PATH = "./engagement.db"

MIN_ENERGY = 0.0
MAX_ENERGY = 100.0
HYSTERESIS_MARGIN = 3.0
DEFAULT_BASE_WEIGHT = 20.0

# Limiares superiores entre niveis: L1|L2, L2|L3, L3|L4, L4|L5.
LEVEL_BOUNDARIES = (15.0, 35.0, 60.0, 85.0)

# Buckets de meia-vida por faixa de energia (N0 <= limite -> t_1/2 em dias).
# L1/L2 (0-35): 7 dias | L3 (35-60): 15 dias | L4/L5 (60-100): 30 dias.
_HALF_LIFE_UPPER_BOUNDS = np.array([35.0, 60.0, 100.0])
_HALF_LIFE_DAYS = np.array([7.0, 15.0, 30.0])


# --------------------------------------------------------------------------
# Matematica vetorizada (numpy puro, sem laco por linha)
# --------------------------------------------------------------------------

def half_life_days(n0: np.ndarray) -> np.ndarray:
    """t_1/2 em dias para cada valor de N0, conforme a faixa de energia (bucket)."""
    n0 = np.asarray(n0, dtype=np.float64)
    idx = np.searchsorted(_HALF_LIFE_UPPER_BOUNDS, n0, side="left")
    idx = np.clip(idx, 0, len(_HALF_LIFE_DAYS) - 1)
    return _HALF_LIFE_DAYS[idx]


def decay(n0: np.ndarray, delta_days: np.ndarray) -> np.ndarray:
    """N(t) = N0 * e^(-lambda * dt), vetorizado. dt negativo e tratado como 0."""
    n0 = np.asarray(n0, dtype=np.float64)
    delta_days = np.maximum(np.asarray(delta_days, dtype=np.float64), 0.0)
    lam = np.log(2.0) / half_life_days(n0)
    return n0 * np.exp(-lam * delta_days)


def apply_energy_gain(n_decayed: np.ndarray, delta_e: np.ndarray) -> np.ndarray:
    """N0_novo = min(N(t) + delta_E, 100), com piso em 0 por seguranca numerica."""
    n_decayed = np.asarray(n_decayed, dtype=np.float64)
    delta_e = np.asarray(delta_e, dtype=np.float64)
    return np.clip(n_decayed + delta_e, MIN_ENERGY, MAX_ENERGY)


def classify_level_raw(n: np.ndarray) -> np.ndarray:
    """Nivel (1-5) a partir apenas do valor de energia, sem histerese."""
    n = np.asarray(n, dtype=np.float64)
    return 1 + np.searchsorted(np.array(LEVEL_BOUNDARIES), n, side="left").clip(
        0, len(LEVEL_BOUNDARIES)
    )


def apply_hysteresis(prior_level: np.ndarray, n: np.ndarray) -> np.ndarray:
    """
    Transicao de nivel com histerese, vetorizada sobre todos os autores de uma vez.

    O laco abaixo roda sobre os 4 limiares fixos entre niveis (constante,
    nao sobre autores/linhas): para cada limiar, o autor "esta acima" se
    N cruzou o limiar de subida (estrito), "esta abaixo" se caiu alem da
    margem de histerese, ou mantem o estado anterior caso esteja na faixa
    intermediaria (a "zona morta" que evita oscilacao).
    """
    prior_level = np.asarray(prior_level, dtype=np.int64)
    n = np.asarray(n, dtype=np.float64)

    new_level = np.ones_like(prior_level)
    for i, boundary in enumerate(LEVEL_BOUNDARIES, start=1):
        prior_above = prior_level > i
        now_above = np.where(
            n >= boundary,
            True,
            np.where(n < boundary - HYSTERESIS_MARGIN, False, prior_above),
        )
        new_level = new_level + now_above.astype(np.int64)

    return new_level


def _down_threshold_for_level(level: np.ndarray) -> np.ndarray:
    """Limiar inferior (ja com a margem de histerese) do nivel atual de cada autor."""
    level = np.asarray(level, dtype=np.int64)
    idx = np.clip(level - 2, 0, len(LEVEL_BOUNDARIES) - 1)
    lower = np.array(LEVEL_BOUNDARIES, dtype=np.float64)[idx]
    return lower - HYSTERESIS_MARGIN


# --------------------------------------------------------------------------
# Atualizacao diaria em lote (todos os autores de uma vez)
# --------------------------------------------------------------------------

def run_daily_batch(
    state: pd.DataFrame,
    day_events: pd.DataFrame,
    as_of: pd.Timestamp,
    base_weight: float = DEFAULT_BASE_WEIGHT,
) -> pd.DataFrame:
    """
    Aplica um passo diario do motor sobre TODOS os autores simultaneamente.

    state: index=author_channel_id, colunas [energy, level, last_update_at, last_event_at].
    day_events: colunas [author_channel_id, quality_score] com os eventos do dia `as_of`.
    Retorna um novo DataFrame de estado (mesma forma), atualizado.
    """
    as_of = pd.Timestamp(as_of)
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")

    delta_days = (as_of - state["last_update_at"]).dt.total_seconds().to_numpy() / 86400.0
    decayed = decay(state["energy"].to_numpy(), delta_days)

    if day_events.empty:
        delta_e = np.zeros(len(state))
    else:
        somado = day_events.groupby("author_channel_id")["quality_score"].sum() * base_weight
        delta_e = somado.reindex(state.index, fill_value=0.0).to_numpy()

    new_energy = apply_energy_gain(decayed, delta_e)
    new_level = apply_hysteresis(state["level"].to_numpy(), new_energy)

    had_event = delta_e > 0
    last_event_at = state["last_event_at"].copy()
    if had_event.any():
        last_event_at.loc[state.index[had_event]] = as_of

    updated = state.copy()
    updated["energy"] = new_energy
    updated["level"] = new_level
    updated["last_update_at"] = as_of
    updated["last_event_at"] = last_event_at
    updated["updated_at"] = pd.Timestamp.now(tz="UTC")
    return updated


def project_to_now(state: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """
    Retorna uma copia de `state` com a energia projetada (decaida) ate `as_of`
    (default: agora), sem consumir novos eventos nem persistir nada. Usado
    para exibir "energia atual" no dashboard entre execucoes do job diario.
    """
    as_of = pd.Timestamp(as_of or pd.Timestamp.now(tz="UTC"))
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")

    delta_days = (as_of - state["last_update_at"]).dt.total_seconds().to_numpy() / 86400.0
    projected = state.copy()
    projected["energy"] = decay(state["energy"].to_numpy(), delta_days)
    projected["as_of"] = as_of
    return projected


def churn_risk_report(
    state: pd.DataFrame, buffer: float = 10.0, as_of: pd.Timestamp | None = None
) -> pd.DataFrame:
    """
    Super-fas (L4/L5) cuja energia projetada para `as_of` esta a `buffer`
    pontos ou menos do limiar de queda de nivel (ja considerando a margem
    de histerese) -- ou seja, em risco iminente de evasao.

    `level` no estado persistido so e recalculado quando o job diario roda;
    entre uma execucao e outra, a energia ja pode ter decaido a ponto de
    "dever" um nivel mais baixo. Por isso o relatorio tambem calcula
    `projected_level` (aplicando a mesma histerese sobre a energia
    projetada) em vez de reexibir cegamente o `level` desatualizado.
    """
    candidates = state[state["level"] >= 4]
    if candidates.empty:
        return candidates.assign(projected_energy=pd.Series(dtype="float64"),
                                  projected_level=pd.Series(dtype="int64"),
                                  distance_to_demotion=pd.Series(dtype="float64"))

    projected = project_to_now(candidates, as_of=as_of)
    projected_energy = projected["energy"].to_numpy()
    prior_level = candidates["level"].to_numpy()
    down_threshold = _down_threshold_for_level(prior_level)

    result = candidates.copy()
    result["projected_energy"] = projected_energy
    result["projected_level"] = apply_hysteresis(prior_level, projected_energy)
    result["distance_to_demotion"] = projected_energy - down_threshold

    at_risk = result[result["distance_to_demotion"] <= buffer]
    return at_risk.sort_values("distance_to_demotion")


def init_state_for_authors(author_ids: list[str], as_of: pd.Timestamp) -> pd.DataFrame:
    """Estado inicial (energia zero, nivel 1) para uma lista de autores."""
    index = pd.Index(author_ids, name="author_channel_id")
    return pd.DataFrame(
        {
            "energy": 0.0,
            "level": 1,
            "last_update_at": as_of,
            # dtype explicito: uma coluna so com NaT vira tz-naive por padrao, o que
            # quebra a atribuicao posterior de timestamps tz-aware (UTC) nela.
            "last_event_at": pd.Series(pd.NaT, index=index, dtype="datetime64[ns, UTC]"),
            "updated_at": as_of,
        },
        index=index,
    )


def backfill_history(
    events: pd.DataFrame,
    base_weight: float = DEFAULT_BASE_WEIGHT,
    keep_history: bool = False,
    initial_state: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """
    Reprocessa um DataFrame de eventos (colunas: author_channel_id,
    published_at, quality_score) dia a dia, do primeiro ao ultimo evento.

    O laco em Python aqui percorre DIAS (bounded pelo range de datas do
    dataset), nao linhas de evento: cada iteracao processa um dia (operacao
    vetorizada de pandas) e chama run_daily_batch, que atualiza todos os
    autores de uma vez. Isso e o que viabiliza escalar para milhoes de
    eventos sem iterrows e sem estourar memoria (o dataframe de eventos
    completo so precisa estar em memoria uma vez; o estado por autor e
    pequeno).

    Duas otimizacoes sobre a versao ingenua: (1) os eventos sao agrupados
    por dia UMA UNICA VEZ via groupby (nao ha refiltragem O(n) do
    dataframe inteiro a cada iteracao); (2) o laco pula direto para o
    proximo dia que TEM pelo menos um evento, em vez de andar dia a dia
    pelo calendario inteiro -- valido porque decay() ja aceita qualquer
    delta_days, entao pular dias vazios e matematicamente identico a
    passar por eles, so que muito mais rapido em datasets esparsos ao
    longo de varios anos (ex.: um canal com 10 anos de historico mas
    atividade concentrada em alguns dias por semana).

    `initial_state` permite atualizacao INCREMENTAL: em vez de reprocessar
    o historico inteiro, passe aqui o estado ja persistido (de
    `load_state`) e `events` contendo so os eventos NOVOS desde a ultima
    execucao. Autores ja conhecidos continuam de onde pararam (o decaimento
    entre `last_update_at` e o novo evento e aplicado normalmente); autores
    novos sao inicializados do zero.
    """
    events = events.copy()
    events["published_at"] = pd.to_datetime(events["published_at"], utc=True)
    events["day"] = events["published_at"].dt.floor("D")

    author_ids = sorted(events["author_channel_id"].unique())
    first_day = events["day"].min()

    if initial_state is None or initial_state.empty:
        state = init_state_for_authors(author_ids, as_of=first_day)
    else:
        autores_novos = [a for a in author_ids if a not in initial_state.index]
        if autores_novos:
            state = pd.concat([initial_state, init_state_for_authors(autores_novos, as_of=first_day)])
        else:
            state = initial_state.copy()

    grouped = {day: df[["author_channel_id", "quality_score"]] for day, df in events.groupby("day")}

    history_frames = [] if keep_history else None

    for day in sorted(grouped):
        day_events = grouped[day]
        as_of = day + pd.Timedelta(hours=23, minutes=59, seconds=59)
        state = run_daily_batch(state, day_events, as_of=as_of, base_weight=base_weight)
        if keep_history:
            snapshot = state.reset_index().assign(date=day)
            history_frames.append(snapshot)

    history = pd.concat(history_frames, ignore_index=True) if keep_history else None
    return state, history


# --------------------------------------------------------------------------
# Persistencia (SQLite)
# --------------------------------------------------------------------------

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def insert_events(conn: sqlite3.Connection, events: pd.DataFrame) -> None:
    """Insere (ou atualiza) autores e eventos de engajamento a partir de um DataFrame."""
    autores = (
        events[["author_channel_id", "author_display_name"]]
        .drop_duplicates("author_channel_id")
        .copy()
    )
    now_iso = pd.Timestamp.now(tz="UTC").isoformat()
    autores["first_seen_at"] = now_iso
    autores["last_seen_at"] = now_iso
    conn.executemany(
        """
        INSERT INTO authors (author_channel_id, author_display_name, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(author_channel_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
        """,
        autores[["author_channel_id", "author_display_name", "first_seen_at", "last_seen_at"]].values.tolist(),
    )

    colunas = ["event_id", "comment_id", "author_channel_id", "video_id", "published_at",
               "quality_score", "categorias"]
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO engagement_events ({", ".join(colunas)})
        VALUES ({", ".join("?" for _ in colunas)})
        """,
        events[colunas].values.tolist(),
    )
    conn.commit()


def load_events_for_day(conn: sqlite3.Connection, day: pd.Timestamp) -> pd.DataFrame:
    """Carrega so os eventos de UM dia (usa o indice em published_at)."""
    start = day.strftime("%Y-%m-%dT00:00:00")
    end = day.strftime("%Y-%m-%dT23:59:59")
    return pd.read_sql_query(
        "SELECT author_channel_id, quality_score FROM engagement_events "
        "WHERE published_at BETWEEN ? AND ?",
        conn,
        params=(start, end),
    )


def load_state(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM author_engagement_state", conn)
    if df.empty:
        return df.set_index("author_channel_id")
    df["last_update_at"] = pd.to_datetime(df["last_update_at"], utc=True)
    df["last_event_at"] = pd.to_datetime(df["last_event_at"], utc=True)
    df["updated_at"] = pd.to_datetime(df["updated_at"], utc=True)
    return df.set_index("author_channel_id")


def save_state(conn: sqlite3.Connection, state: pd.DataFrame) -> None:
    rows = state.reset_index()
    rows["last_update_at"] = rows["last_update_at"].apply(lambda t: t.isoformat() if pd.notna(t) else None)
    rows["last_event_at"] = rows["last_event_at"].apply(lambda t: t.isoformat() if pd.notna(t) else None)
    rows["updated_at"] = rows["updated_at"].apply(lambda t: t.isoformat() if pd.notna(t) else None)
    conn.executemany(
        """
        INSERT INTO author_engagement_state
            (author_channel_id, energy, level, last_update_at, last_event_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(author_channel_id) DO UPDATE SET
            energy = excluded.energy,
            level = excluded.level,
            last_update_at = excluded.last_update_at,
            last_event_at = excluded.last_event_at,
            updated_at = excluded.updated_at
        """,
        rows[["author_channel_id", "energy", "level", "last_update_at", "last_event_at", "updated_at"]]
        .values.tolist(),
    )
    conn.commit()
