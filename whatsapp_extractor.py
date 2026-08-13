"""
Extrai eventos de engajamento de um export de chat do WhatsApp (.txt),
pontuando cada mensagem com HeuristicScorer (sem custo de LLM) e gravando
no schema universal de eventos (mesmo contrato consumido pelo adaptador
YouTube em build_engagement_state.py).

So suporta o formato de export padrao Android:
    DD/MM/AAAA HH:MM - Autor: mensagem

Uso:
    python whatsapp_extractor.py --input data/whatsapp_bruto_grupo.txt --grupo "Nome do Grupo"
"""

import argparse
import hashlib
import logging
import re
from pathlib import Path

import pandas as pd

from scoring_engine import HeuristicScorer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("whatsapp_extractor")

DEFAULT_OUTPUT_PATH = "./data/whatsapp_eventos.json"

TIMESTAMP_PREFIX_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4} \d{1,2}:\d{2} - ")
LINE_PATTERN = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}) (\d{1,2}:\d{2}) - ([^:]+): (.*)$")

SYSTEM_MESSAGE_PATTERNS = [
    re.compile(r"m[ií]dia oculta", re.IGNORECASE),
    re.compile(r"figurinha", re.IGNORECASE),
    re.compile(r"[aá]udio omitido", re.IGNORECASE),
    re.compile(r"esta mensagem foi apagada", re.IGNORECASE),
    re.compile(r"voc[eê] apagou esta mensagem", re.IGNORECASE),
    re.compile(r"documento omitido", re.IGNORECASE),
]

# Linhas de notificacao de grupo (sem "Autor:", nao casam com LINE_PATTERN):
# entrada/saida/adicao/remocao de membro, mudanca de nome/descricao do grupo,
# aviso de criptografia. Usadas so pra reconhecer e DESCARTAR essas linhas
# em parse_whatsapp_export -- nunca sao tratadas como continuacao da
# mensagem anterior nem geram autor "fantasma".
SYSTEM_NOTIFICATION_PATTERNS = [
    re.compile(r"entrou usando o link de convite deste grupo", re.IGNORECASE),
    re.compile(r"entrou no grupo", re.IGNORECASE),
    re.compile(r"^.+ saiu$", re.IGNORECASE),
    re.compile(r"^.+ adicionou .+", re.IGNORECASE),
    re.compile(r"^.+ removeu .+", re.IGNORECASE),
    re.compile(r"mudou o nome do grupo para", re.IGNORECASE),
    re.compile(r"mudou a descri[cç][aã]o do grupo", re.IGNORECASE),
    re.compile(r"mudou a imagem do grupo", re.IGNORECASE),
    re.compile(r"criou o grupo", re.IGNORECASE),
    re.compile(r"as mensagens e as liga[cç][oõ]es agora s[aã]o protegidas", re.IGNORECASE),
]


def is_system_notification_line(linha_sem_prefixo: str) -> bool:
    """Linha de notificacao de grupo (join/leave/added/removed/mudanca de
    nome ou descricao/criptografia) -- sem 'Autor:', nunca deve virar
    mensagem nem continuacao da anterior."""
    return any(padrao.search(linha_sem_prefixo) for padrao in SYSTEM_NOTIFICATION_PATTERNS)


def parse_whatsapp_export(texto: str) -> list[dict]:
    """
    Parseia o texto bruto do export, uma mensagem por dict com chaves
    'author', 'timestamp_raw', 'text'.

    Uma linha SO pode ser continuacao da mensagem anterior se ela NAO
    comecar com o prefixo de timestamp 'DD/MM/AAAA HH:MM - ' (quebra de
    linha real dentro de uma mensagem). Qualquer linha com esse prefixo e
    ou (a) uma nova mensagem 'Autor: texto', ou (b) uma notificacao de
    sistema/grupo (join, saida, adicao, remocao, mudanca de nome/descricao
    do grupo, aviso de criptografia) -- nunca e concatenada na mensagem
    anterior. Algumas notificacoes contem ':' no meio da frase (ex.: 'Ana
    mudou o nome do grupo para: X'), o que faria LINE_PATTERN confundi-las
    com 'Autor: mensagem' (autor fantasma = 'Ana mudou o nome do grupo
    para'); por isso o texto extraido como 'autor' e checado contra os
    padroes de notificacao ANTES de aceitar o casamento. Linhas com prefixo
    de timestamp que nao casam LINE_PATTERN nem um padrao conhecido de
    notificacao sao apenas descartadas com aviso no log.
    """
    mensagens: list[dict] = []
    for linha in texto.splitlines():
        linha = linha.rstrip("\n")
        if not linha.strip():
            continue

        match = LINE_PATTERN.match(linha)
        if match:
            data_raw, hora_raw, autor, corpo = match.groups()
            autor = autor.strip()
            if is_system_notification_line(autor):
                continue
            mensagens.append(
                {
                    "author": autor,
                    "timestamp_raw": f"{data_raw} {hora_raw}",
                    "text": corpo.strip(),
                }
            )
        elif TIMESTAMP_PREFIX_PATTERN.match(linha):
            # Tem prefixo de timestamp mas nao casa 'Autor: mensagem' --
            # nao ha ':' na linha (notificacoes de sistema tipicamente nao
            # tem "Autor:"). Nunca e continuacao da mensagem anterior.
            resto = TIMESTAMP_PREFIX_PATTERN.sub("", linha, count=1)
            if not is_system_notification_line(resto):
                logger.warning("Linha de sistema nao reconhecida, ignorando: %r", linha[:80])
        elif mensagens:
            mensagens[-1]["text"] += "\n" + linha.strip()
        else:
            logger.warning("Linha nao reconhecida antes de qualquer mensagem, ignorando: %r", linha[:80])
    return mensagens


def is_system_message(texto: str) -> bool:
    return any(padrao.search(texto) for padrao in SYSTEM_MESSAGE_PATTERNS)


def make_event_id(author: str, published_at: str, text: str) -> str:
    """Hash deterministico -- export do WhatsApp nao tem ID nativo de mensagem."""
    payload = f"{author}|{published_at}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def parse_timestamp(timestamp_raw: str) -> str:
    """Converte 'DD/MM/AAAA HH:MM' pro ISO 8601 UTC. Assume que o horario do
    export ja esta no fuso que se quer usar como referencia (export nao traz
    fuso horario) -- limitacao conhecida, documentada no README."""
    dt = pd.to_datetime(timestamp_raw, format="%d/%m/%Y %H:%M", errors="coerce")
    if pd.isna(dt):
        dt = pd.to_datetime(timestamp_raw, dayfirst=True, errors="coerce")
    return dt.tz_localize("UTC").isoformat()


UNIVERSAL_COLUMNS = [
    "event_id", "platform", "author_id", "author_display_name",
    "content_id", "published_at", "quality_score", "categorias",
]


def build_whatsapp_events(mensagens: list[dict], grupo: str) -> pd.DataFrame:
    """Filtra mensagens de sistema, pontua com HeuristicScorer e monta o
    schema universal de eventos (platform='whatsapp')."""
    validas = [m for m in mensagens if not is_system_message(m["text"])]
    if not validas:
        return pd.DataFrame(columns=UNIVERSAL_COLUMNS)

    scorer = HeuristicScorer()
    published_at = [parse_timestamp(m["timestamp_raw"]) for m in validas]
    scores = scorer.score_batch([{"text": m["text"]} for m in validas])

    rows = []
    for m, pub, score in zip(validas, published_at, scores):
        rows.append(
            {
                "event_id": make_event_id(m["author"], pub, m["text"]),
                "platform": "whatsapp",
                "author_id": m["author"],
                "author_display_name": m["author"],
                "content_id": grupo,
                "published_at": pub,
                "quality_score": score,
                "categorias": "",
            }
        )
    df = pd.DataFrame(rows, columns=UNIVERSAL_COLUMNS)
    return df.drop_duplicates("event_id")


def save_events(events: pd.DataFrame, output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    events.to_json(output_path, orient="records", force_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Caminho do .txt exportado do WhatsApp")
    parser.add_argument("--grupo", required=True, help="Nome do grupo/chat (vira content_id)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    texto = Path(args.input).read_text(encoding="utf-8")
    mensagens = parse_whatsapp_export(texto)
    logger.info("%d mensagens parseadas de %s", len(mensagens), args.input)

    events = build_whatsapp_events(mensagens, args.grupo)
    logger.info("%d eventos validos (apos filtro de mensagens de sistema)", len(events))

    save_events(events, args.output)
    logger.info("Salvo em: %s", args.output)


if __name__ == "__main__":
    main()
