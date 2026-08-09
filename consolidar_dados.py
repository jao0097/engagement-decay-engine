"""
ETL do sprint omnichannel: normaliza data/dados_brutos.csv para o schema
unificado, calcula metricas derivadas e valida antes de salvar.

Uso:
    python consolidar_dados.py
"""

import logging
import os

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("consolidar_dados")

RAW_INPUT_PATH = "./data/dados_brutos.csv"
CONSOLIDATED_OUTPUT_PATH = "./data/dados_consolidados_omnichannel.csv"

UNIFIED_COLUMNS = [
    "platform",
    "content_id",
    "title",
    "channel_title",
    "username",
    "subreddit",
    "created_at",
    "likes",
    "comments",
    "views",
    "engagement_total",
    "hours_since_publish",
]

NUMERIC_COLUMNS = ["likes", "comments", "views"]
STRING_COLUMNS = ["platform", "content_id", "title", "channel_title", "username", "subreddit", "created_at"]


def truncar_title(texto, max_len: int = 280) -> str:
    if not isinstance(texto, str):
        return ""
    return texto[:max_len]


def calcular_engagement_total(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["engagement_total"] = df["likes"] + df["comments"] * 2
    return df


def calcular_hours_since_publish(df: pd.DataFrame, agora=None) -> pd.DataFrame:
    df = df.copy()
    if agora is None:
        from datetime import datetime, timezone

        agora = datetime.now(timezone.utc)
    created = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    agora_ts = pd.Timestamp(agora)
    if agora_ts.tzinfo is None:
        agora_ts = agora_ts.tz_localize("UTC")
    delta = agora_ts - created
    df["hours_since_publish"] = delta.dt.total_seconds() / 3600
    return df


def normalizar_schema(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for col in STRING_COLUMNS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    for col in NUMERIC_COLUMNS:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["title"] = df["title"].apply(truncar_title)

    df = calcular_engagement_total(df)
    df["engagement_total"] = df["engagement_total"].astype(int)

    df = calcular_hours_since_publish(df)

    df = df.drop_duplicates(subset=["content_id"], keep="first")

    return df[UNIFIED_COLUMNS]


def validar_schema(df: pd.DataFrame) -> None:
    faltando = set(UNIFIED_COLUMNS) - set(df.columns)
    if faltando:
        raise ValueError(f"Colunas faltando no dataframe consolidado: {faltando}")
    for col in NUMERIC_COLUMNS + ["engagement_total"]:
        if df[col].isna().any():
            raise ValueError(f"Coluna numerica {col} contem NaN apos normalizacao")


def main():
    if not os.path.exists(RAW_INPUT_PATH):
        logger.error("Arquivo %s nao encontrado. Rode coleta_sprint_4h.py primeiro.", RAW_INPUT_PATH)
        return

    logger.info("Carregando %s", RAW_INPUT_PATH)
    # keep_default_na=False: evita que campos string vazios ("") virem NaN/float
    # ao ler de volta o CSV (username/subreddit sao vazios para plataformas
    # onde nao se aplicam, e devem continuar sendo string vazia, nao NaN).
    df_bruto = pd.read_csv(RAW_INPUT_PATH, keep_default_na=False)
    logger.info("%d linhas brutas carregadas", len(df_bruto))

    df_consolidado = normalizar_schema(df_bruto)
    validar_schema(df_consolidado)
    logger.info("%d linhas apos normalizacao e dedup", len(df_consolidado))

    os.makedirs(os.path.dirname(CONSOLIDATED_OUTPUT_PATH), exist_ok=True)
    df_consolidado.to_csv(CONSOLIDATED_OUTPUT_PATH, index=False)
    logger.info("Salvo em: %s", CONSOLIDATED_OUTPUT_PATH)


if __name__ == "__main__":
    main()
