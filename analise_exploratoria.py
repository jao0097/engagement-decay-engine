"""
Analise exploratoria do dataset consolidado do sprint omnichannel: estatisticas
descritivas por plataforma, distribuicao temporal e histogramas.

Uso:
    python analise_exploratoria.py
"""

import logging
import os

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("analise_exploratoria")

CONSOLIDATED_INPUT_PATH = "./data/dados_consolidados_omnichannel.csv"
HISTOGRAMS_OUTPUT_PATH = "./histogramas.png"
REPORT_OUTPUT_PATH = "./relatorio_exploratorio.txt"

METRICAS = ["likes", "comments", "views", "engagement_total"]


def bucketizar_tempo(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    bins = [-float("inf"), 24, 168, float("inf")]
    labels = ["0-24h", "1-7 dias", "7+ dias"]
    df["janela_temporal"] = pd.cut(df["hours_since_publish"], bins=bins, labels=labels)
    return df


def calcular_estatisticas_por_plataforma(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby("platform")[METRICAS].agg(["mean", "median", "std"])


def gerar_histogramas(df: pd.DataFrame, output_path: str) -> None:
    plataformas = sorted(df["platform"].unique())
    fig, axes = plt.subplots(1, len(plataformas), figsize=(6 * len(plataformas), 5), squeeze=False)
    for i, plataforma in enumerate(plataformas):
        ax = axes[0][i]
        dados = df[df["platform"] == plataforma]["engagement_total"]
        ax.hist(dados, bins=20, color="steelblue", edgecolor="black")
        ax.set_title(f"Engagement total — {plataforma}")
        ax.set_xlabel("engagement_total")
        ax.set_ylabel("frequencia")
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)


def gerar_relatorio(df: pd.DataFrame, stats: pd.DataFrame, bucket_counts: pd.DataFrame, output_path: str) -> None:
    linhas = []
    linhas.append("RELATORIO EXPLORATORIO — SPRINT OMNICHANNEL")
    linhas.append("=" * 50)
    linhas.append(f"Total de registros: {len(df)}")
    linhas.append(f"Plataformas: {', '.join(sorted(df['platform'].unique()))}")
    linhas.append("")
    linhas.append("ESTATISTICAS DESCRITIVAS POR PLATAFORMA")
    linhas.append("-" * 50)
    linhas.append(stats.to_string())
    linhas.append("")
    linhas.append("DISTRIBUICAO POR JANELA TEMPORAL DE PUBLICACAO")
    linhas.append("-" * 50)
    linhas.append(bucket_counts.to_string())
    linhas.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))


def main():
    if not os.path.exists(CONSOLIDATED_INPUT_PATH):
        logger.error("Arquivo %s nao encontrado. Rode consolidar_dados.py primeiro.", CONSOLIDATED_INPUT_PATH)
        return

    logger.info("Carregando %s", CONSOLIDATED_INPUT_PATH)
    df = pd.read_csv(CONSOLIDATED_INPUT_PATH)
    logger.info("%d registros carregados", len(df))

    df = bucketizar_tempo(df)
    stats = calcular_estatisticas_por_plataforma(df)
    bucket_counts = df.groupby(["platform", "janela_temporal"], observed=True).size().unstack(fill_value=0)

    logger.info("Gerando histogramas em %s", HISTOGRAMS_OUTPUT_PATH)
    gerar_histogramas(df, HISTOGRAMS_OUTPUT_PATH)

    logger.info("Gerando relatorio em %s", REPORT_OUTPUT_PATH)
    gerar_relatorio(df, stats, bucket_counts, REPORT_OUTPUT_PATH)

    logger.info("Analise exploratoria concluida.")


if __name__ == "__main__":
    main()
