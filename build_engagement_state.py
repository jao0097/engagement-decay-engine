"""
Classifica os comentarios do YouTube nos 5 niveis de engajamento (L1-L5),
usando o motor de decaimento.

Score de qualidade (Q) de cada comentario:
- Se ./data/comentarios_classificados.json existir (saida do main.py via
  Groq), reaproveita as categorias ja classificadas com o
  CategoryWeightedScorer -- sem chamar o LLM de novo.
- Caso contrario, usa o HeuristicScorer local (scoring_engine.py), sem
  gastar nenhum token de API.

O `author` extraido pelo YouTube (@handle, ja unico por usuario) e usado
como identificador de autor.

Modo incremental (padrao quando ./engagement.db ja existe e tem dados):
so processa comentarios com published_at mais novo que o ultimo evento ja
gravado, continuando a partir do estado persistido -- em vez de reprocessar
tudo do zero. Use --rebuild para forcar uma reconstrucao completa.

Uso:
    python build_engagement_state.py [--base-weight 20] [--db ./engagement.db] [--rebuild]

Resultado: ./engagement.db povoado com author_engagement_state (energia e
nivel L1-L5 por autor) e engagement_events (score por comentario), prontos
para o dashboard (streamlit run app.py) ou para consultas diretas.
"""

import argparse
import json
import time
from pathlib import Path

import pandas as pd

import decay_engine
from scoring_engine import CategoryWeightedScorer, HeuristicScorer

RAW_COMMENTS_PATH = "./data/comentarios_brutos.json"
CLASSIFIED_COMMENTS_PATH = "./data/comentarios_classificados.json"
INSERT_CHUNK_SIZE = 20_000


def load_and_score_comments(raw_path: str, classified_path: str) -> pd.DataFrame:
    """
    Carrega os comentarios e calcula Q em [0,1], preferindo a classificacao
    Groq ja feita (se existir) sobre a heuristica local.
    """
    classified_file = Path(classified_path)
    if classified_file.exists():
        print(f"Encontrado {classified_path} -- reaproveitando classificacao Groq (0 tokens novos).")
        t0 = time.time()
        with open(classified_file, encoding="utf-8") as f:
            raw = json.load(f)
        df = pd.DataFrame(raw)
        del raw
        scorer = CategoryWeightedScorer()
        comentarios = [
            {"categorias": cats, "score_engajamento": score}
            for cats, score in zip(df.get("categorias", [[]] * len(df)), df.get("score_engajamento", [None] * len(df)))
        ]
        df["quality_score"] = scorer.score_batch(comentarios)
        print(f"  {len(df):,} comentarios pontuados (CategoryWeightedScorer) em {time.time() - t0:.1f}s".replace(",", "."))
        return df

    print(f"{classified_path} nao encontrado -- usando HeuristicScorer local (sem LLM).")
    print(f"Carregando {raw_path}...")
    t0 = time.time()
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    del raw
    print(f"  {len(df):,} comentarios carregados em {time.time() - t0:.1f}s".replace(",", "."))

    t0 = time.time()
    scorer = HeuristicScorer()
    textos = df["text"].fillna("")
    comentarios = [{"text": t} for t in textos]
    df["quality_score"] = scorer.score_batch(comentarios)
    print(f"  {len(df):,} comentarios pontuados (HeuristicScorer) em {time.time() - t0:.1f}s".replace(",", "."))
    return df


def build_events_frame(df: pd.DataFrame) -> pd.DataFrame:
    if "categorias" in df.columns:
        categorias = df["categorias"].apply(lambda c: ",".join(c) if isinstance(c, list) else (c or ""))
    else:
        categorias = ""

    events = pd.DataFrame(
        {
            "event_id": df["comment_id"],
            "comment_id": df["comment_id"],
            "author_channel_id": df["author"],
            "author_display_name": df["author"],
            "video_id": df["video_id"],
            "published_at": df["published_at"],
            "quality_score": df["quality_score"],
            "categorias": categorias,
        }
    )
    # comentarios duplicados/reextraidos (mesmo comment_id) so entram uma vez.
    events = events.drop_duplicates("event_id")
    return events


def get_events_cutoff(conn) -> pd.Timestamp | None:
    """Timestamp do evento mais recente ja gravado no banco, ou None se vazio."""
    row = conn.execute("SELECT MAX(published_at) FROM engagement_events").fetchone()
    if row is None or row[0] is None:
        return None
    return pd.Timestamp(row[0])


def insert_events_in_chunks(conn, events: pd.DataFrame, chunk_size: int = INSERT_CHUNK_SIZE) -> None:
    print(f"Inserindo {len(events):,} eventos no SQLite em lotes de {chunk_size:,}...".replace(",", "."))
    t0 = time.time()
    for i in range(0, len(events), chunk_size):
        chunk = events.iloc[i : i + chunk_size]
        decay_engine.insert_events(conn, chunk)
        done = min(i + chunk_size, len(events))
        print(f"  {done:,}/{len(events):,} eventos gravados".replace(",", "."))
    print(f"  concluido em {time.time() - t0:.1f}s")


def instant_level_distribution(events: pd.DataFrame) -> pd.Series:
    """
    Nivel 'instantaneo' de CADA comentario isolado (score do proprio comentario
    x 100, sem decaimento nem historico) -- complementar ao nivel do AUTOR,
    que e o que o motor de decaimento calcula (energia acumulada com decaimento
    no tempo). Serve so como leitura rapida de "que qualidade os comentarios
    tem", nao para identificar super-fas (isso e o nivel do autor).
    """
    niveis = decay_engine.classify_level_raw((events["quality_score"] * 100.0).to_numpy())
    return pd.Series(niveis).value_counts().sort_index()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-weight", type=float, default=decay_engine.DEFAULT_BASE_WEIGHT)
    parser.add_argument("--db", type=str, default=decay_engine.DB_PATH)
    parser.add_argument("--raw", type=str, default=RAW_COMMENTS_PATH)
    parser.add_argument("--classified", type=str, default=CLASSIFIED_COMMENTS_PATH)
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Forca reconstrucao completa do banco, mesmo se ele ja existir com dados.",
    )
    args = parser.parse_args()

    t_inicio = time.time()

    df = load_and_score_comments(args.raw, args.classified)
    events = build_events_frame(df)
    del df  # libera o dataframe bruto (com o texto completo) antes do backfill

    db_path = Path(args.db)
    banco_existente = db_path.exists()

    if banco_existente and args.rebuild:
        db_path.unlink()
        print(f"\n--rebuild: banco anterior {db_path} removido para reconstrucao completa.")
        banco_existente = False

    conn = decay_engine.get_connection(str(db_path))
    decay_engine.init_schema(conn)

    cutoff = get_events_cutoff(conn) if banco_existente else None
    estado_existente = decay_engine.load_state(conn) if banco_existente else None
    modo_incremental = cutoff is not None and estado_existente is not None and not estado_existente.empty

    if modo_incremental:
        antes = len(events)
        events = events[pd.to_datetime(events["published_at"], utc=True) > cutoff]
        print(
            f"\nModo incremental: banco ja tem eventos ate {cutoff}. "
            f"{len(events):,}/{antes:,} comentarios sao novos.".replace(",", ".")
        )
        if events.empty:
            print("Nada novo para processar. Banco ja esta atualizado.")
            conn.close()
            print(f"\nTotal: {time.time() - t_inicio:.1f}s.")
            return
    else:
        print(f"\nModo completo: processando os {len(events):,} comentarios do zero.".replace(",", "."))

    print("\n=== Distribuicao de nivel INSTANTANEO por comentario (score isolado) ===")
    for nivel, contagem in instant_level_distribution(events).items():
        print(f"  L{nivel}: {contagem:,} comentarios".replace(",", "."))

    insert_events_in_chunks(conn, events)

    print("\nExecutando motor de decaimento (replay dia a dia dos eventos novos)...")
    t0 = time.time()
    state, _ = decay_engine.backfill_history(
        events, base_weight=args.base_weight, keep_history=False, initial_state=estado_existente
    )
    print(f"  {len(state):,} autores atualizados em {time.time() - t0:.1f}s".replace(",", "."))

    decay_engine.save_state(conn, state)
    conn.close()

    print("\n=== Distribuicao final de autores por NIVEL DE ENGAJAMENTO (energia com decaimento) ===")
    labels = {1: "L1 - Novato", 2: "L2 - Casual", 3: "L3 - Engajado", 4: "L4 - Fa", 5: "L5 - Super-fa"}
    for nivel, contagem in state["level"].value_counts().sort_index().items():
        pct = 100 * contagem / len(state)
        print(f"  {labels[nivel]:16s} {contagem:>7,} autores ({pct:5.1f}%)".replace(",", "."))

    risco = decay_engine.churn_risk_report(state, buffer=10.0)
    print(f"\nSuper-fas (L4/L5) em risco iminente de evasao: {len(risco):,}".replace(",", "."))

    print(f"\nTotal: {time.time() - t_inicio:.1f}s. Banco salvo em {db_path}.")
    print("Rode 'streamlit run app.py' para explorar visualmente.")


if __name__ == "__main__":
    main()
