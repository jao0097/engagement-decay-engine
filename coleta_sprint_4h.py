"""
Coleta concorrente de engajamento publico em YouTube, Reddit e Instagram,
para alimentar o modelo de decaimento com um dataset multicanal.

Sprint de 4h, orcamento $0: YouTube via requests puro (REST direto, sem
googleapiclient), Reddit via endpoint publico (sem OAuth), Instagram via
scraping anonimo de HTML (sem login). Cada plataforma roda numa thread
propria; falha em uma nao derruba as outras.

Uso:
    python coleta_sprint_4h.py \
        --youtube UC_x,https://youtube.com/@handle \
        --reddit r/python,dataisbeautiful \
        --instagram nasa,@natgeo

    python coleta_sprint_4h.py   # usa alvos default de teste
"""

import argparse
import csv
import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("coleta_sprint_4h")

RAW_OUTPUT_PATH = "./data/dados_brutos.csv"

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REDDIT_USER_AGENT = "coleta-sprint-4h/1.0 (script de pesquisa de engajamento publico)"

DEFAULT_YOUTUBE_TARGETS = ["@NASA", "@nature"]
DEFAULT_REDDIT_TARGETS = ["dataisbeautiful", "technology", "programming"]
DEFAULT_INSTAGRAM_TARGETS = ["nasa", "natgeo"]

CSV_FIELDS = [
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
]


def normalizar_youtube_target(raw: str) -> str:
    """Aceita ID cru (UC...), URL de canal ou handle (com/sem @), devolve ID cru ou '@handle'."""
    raw = raw.strip()
    match = re.search(r"youtube\.com/channel/([A-Za-z0-9_-]+)", raw)
    if match:
        return match.group(1)
    match = re.search(r"youtube\.com/@([A-Za-z0-9_.-]+)", raw)
    if match:
        return f"@{match.group(1)}"
    if raw.startswith("UC"):
        return raw
    if raw.startswith("@"):
        return raw
    return f"@{raw}"


def normalizar_reddit_target(raw: str) -> str:
    """Aceita URL completa, 'r/nome' ou nome cru, devolve so o nome do subreddit."""
    raw = raw.strip().rstrip("/")
    match = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", raw)
    if match:
        return match.group(1)
    if raw.startswith("r/"):
        return raw[2:]
    return raw


def normalizar_instagram_target(raw: str) -> str:
    """Aceita URL completa, '@user' ou username cru, devolve so o username."""
    raw = raw.strip().rstrip("/")
    match = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", raw)
    if match:
        return match.group(1)
    if raw.startswith("@"):
        return raw[1:]
    return raw
