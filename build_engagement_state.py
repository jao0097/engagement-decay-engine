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

    raw_file = Path(raw_path)
    if not raw_file.exists():
        print(f"{raw_path} nao encontrado -- nenhum comentario do YouTube para processar (plataforma sera pulada).")
        return pd.DataFrame(columns=["comment_id", "author", "video_id", "published_at", "quality_score", "categorias"])

    print(f"Carregando {raw_path}...")
    t0 = time.time()
    with open(raw_file, encoding="utf-8") as f:
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


def youtube_to_universal_events(df: pd.DataFrame) -> pd.DataFrame:
    """Converte comentarios do YouTube (ja com quality_score) para o schema
    universal de eventos (mesmo contrato que qualquer outro adaptador de
    plataforma deve produzir)."""
    if "categorias" in df.columns:
        categorias = df["categorias"].apply(lambda c: ",".join(c) if isinstance(c, list) else (c or ""))
    else:
        categorias = ""

    events = pd.DataFrame(
        {
            "event_id": df["comment_id"],
            "platform": "youtube",
            "author_id": df["author"],
            "author_display_name": df["author"],
            "content_id": df["video_id"],
            "published_at": df["published_at"],
            "quality_score": df["quality_score"],
            "categorias": categorias,
        }
    )
    # comentarios duplicados/reextraidos (mesmo event_id) so entram uma vez.
    events = events.drop_duplicates("event_id")
    return events


def to_decay_engine_events(universal_events: pd.DataFrame) -> pd.DataFrame:
    """Traduz o schema universal (author_id + platform) para o formato interno
    do decay_engine. Namespacia author_channel_id = "{platform}:{author_id}"
    para evitar colisao entre plataformas sem precisar de chave composta em
    pandas -- decay_engine.py continua so enxergando um author_channel_id
    string, sem saber que carrega a plataforma embutida."""
    events = universal_events.copy()
    events["author_channel_id"] = events["platform"] + ":" + events["author_id"].astype(str)
    events["event_source_id"] = events["event_id"]
    return events


def get_events_cutoff(conn, platform: str) -> pd.Timestamp | None:
    """Timestamp do evento mais recente ja gravado para essa plataforma, ou None se vazio."""
    row = conn.execute(
        "SELECT MAX(published_at) FROM engagement_events WHERE platform = ?", (platform,)
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return pd.Timestamp(row[0])


def get_existing_event_ids(conn, platform: str, cutoff: pd.Timestamp) -> set:
    """IDs de eventos ja persistidos para essa plataforma a partir do cutoff
    (inclusive). Usado para deduplicar o corte incremental: como o cutoff e
    o MAX(published_at) ja gravado, plataformas de resolucao grosseira
    (ex.: WhatsApp, so minuto) podem ter mais de um evento no mesmo instante
    do cutoff -- selecionar so '> cutoff' perderia pra sempre qualquer
    mensagem nova que caia nesse mesmo minuto. Selecionando '>= cutoff' e
    excluindo os event_id ja gravados (hash deterministico do conteudo)
    resolve isso sem reaplicar energia de eventos ja processados."""
    rows = conn.execute(
        "SELECT event_id FROM engagement_events WHERE platform = ? AND published_at >= ?",
        (platform, cutoff.isoformat()),
    ).fetchall()
    return {r[0] for r in rows}


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


WHATSAPP_EVENTS_PATH = "./data/whatsapp_eventos.json"

PLATFORM_BASE_WEIGHTS_DEFAULT = {"youtube": decay_engine.DEFAULT_BASE_WEIGHT, "whatsapp": decay_engine.DEFAULT_BASE_WEIGHT}


def load_whatsapp_events(path: str) -> pd.DataFrame:
    events_file = Path(path)
    columns = ["event_id", "platform", "author_id", "author_display_name",
               "content_id", "published_at", "quality_score", "categorias"]
    if not events_file.exists():
        return pd.DataFrame(columns=columns)
    with open(events_file, encoding="utf-8") as f:
        raw = json.load(f)
    if not raw:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(raw)


def process_platform(conn, platform: str, universal_events: pd.DataFrame,
                      base_weight: float, banco_existente: bool) -> None:
    """Processa os eventos de UMA plataforma: cutoff incremental proprio,
    insercao no SQLite e replay do motor de decaimento com o estado
    (filtrado por plataforma) que ja existia."""
    if universal_events.empty:
        print(f"[{platform}] nenhum evento encontrado, pulando.")
        return

    events = to_decay_engine_events(universal_events)

    cutoff = get_events_cutoff(conn, platform) if banco_existente else None
    if cutoff is not None:
        antes = len(events)
        events = events[pd.to_datetime(events["published_at"], utc=True) >= cutoff]
        if not events.empty:
            ja_gravados = get_existing_event_ids(conn, platform, cutoff)
            events = events[~events["event_id"].isin(ja_gravados)]
        print(f"[{platform}] modo incremental: banco ja tem eventos ate {cutoff}. "
              f"{len(events)}/{antes} sao novos.")
        if events.empty:
            print(f"[{platform}] nada novo para processar.")
            return
    else:
        print(f"[{platform}] modo completo: processando {len(events)} eventos do zero.")

    estado_completo = decay_engine.load_state(conn) if banco_existente else pd.DataFrame()
    if not estado_completo.empty:
        estado_plataforma = estado_completo[estado_completo.index.str.startswith(f"{platform}:")]
    else:
        estado_plataforma = estado_completo

    print(f"[{platform}] distribuicao de nivel instantaneo por evento:")
    for nivel, contagem in instant_level_distribution(events).items():
        print(f"  L{nivel}: {contagem} eventos")

    insert_events_in_chunks(conn, events)

    state, _ = decay_engine.backfill_history(
        events, base_weight=base_weight, keep_history=False,
        initial_state=estado_plataforma if not estado_plataforma.empty else None,
    )
    decay_engine.save_state(conn, state)
    print(f"[{platform}] {len(state)} autores atualizados.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-weight-youtube", type=float, default=PLATFORM_BASE_WEIGHTS_DEFAULT["youtube"])
    parser.add_argument("--base-weight-whatsapp", type=float, default=PLATFORM_BASE_WEIGHTS_DEFAULT["whatsapp"])
    parser.add_argument("--db", type=str, default=decay_engine.DB_PATH)
    parser.add_argument("--raw", type=str, default=RAW_COMMENTS_PATH)
    parser.add_argument("--classified", type=str, default=CLASSIFIED_COMMENTS_PATH)
    parser.add_argument("--whatsapp-events", type=str, default=WHATSAPP_EVENTS_PATH)
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Forca reconstrucao completa do banco, mesmo se ele ja existir com dados.",
    )
    args = parser.parse_args()

    t_inicio = time.time()

    db_path = Path(args.db)
    banco_existente = db_path.exists()
    if banco_existente and args.rebuild:
        db_path.unlink()
        print(f"\n--rebuild: banco anterior {db_path} removido para reconstrucao completa.")
        banco_existente = False

    conn = decay_engine.get_connection(str(db_path))
    decay_engine.init_schema(conn)

    df_youtube = load_and_score_comments(args.raw, args.classified)
    eventos_youtube = youtube_to_universal_events(df_youtube)
    del df_youtube

    eventos_whatsapp = load_whatsapp_events(args.whatsapp_events)

    base_weights = {"youtube": args.base_weight_youtube, "whatsapp": args.base_weight_whatsapp}
    for platform, universal_events in (("youtube", eventos_youtube), ("whatsapp", eventos_whatsapp)):
        print(f"\n=== Processando plataforma: {platform} ===")
        process_platform(conn, platform, universal_events, base_weights[platform], banco_existente)

    conn.close()
    print(f"\nTotal: {time.time() - t_inicio:.1f}s. Banco salvo em {db_path}.")
    print("Rode 'streamlit run app.py' para explorar visualmente.")


if __name__ == "__main__":
    main()
