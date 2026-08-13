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

LINE_PATTERN = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}) (\d{1,2}:\d{2}) - ([^:]+): (.*)$")

SYSTEM_MESSAGE_PATTERNS = [
    re.compile(r"m[ií]dia oculta", re.IGNORECASE),
    re.compile(r"figurinha", re.IGNORECASE),
    re.compile(r"[aá]udio omitido", re.IGNORECASE),
    re.compile(r"esta mensagem foi apagada", re.IGNORECASE),
    re.compile(r"voc[eê] apagou esta mensagem", re.IGNORECASE),
    re.compile(r"documento omitido", re.IGNORECASE),
]


def parse_whatsapp_export(texto: str) -> list[dict]:
    """
    Parseia o texto bruto do export, uma mensagem por dict com chaves
    'author', 'timestamp_raw', 'text'. Linhas de continuacao (sem o prefixo
    'DD/MM/AAAA HH:MM - Autor:') sao concatenadas na mensagem anterior.
    Linhas que nao casam com o padrao principal nem tem uma mensagem
    anterior pra continuar sao ignoradas com aviso no log -- cobre a maioria
    das mensagens de sistema, que nao tem "Autor:" (criptografia, entrada/
    saida de membro, etc.).
    """
    mensagens: list[dict] = []
    for linha in texto.splitlines():
        linha = linha.rstrip("\n")
        if not linha.strip():
            continue
        match = LINE_PATTERN.match(linha)
        if match:
            data_raw, hora_raw, autor, corpo = match.groups()
            mensagens.append(
                {
                    "author": autor.strip(),
                    "timestamp_raw": f"{data_raw} {hora_raw}",
                    "text": corpo.strip(),
                }
            )
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


if __name__ == "__main__":
    pass
