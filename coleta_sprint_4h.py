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


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def _youtube_resolve_channel_id(api_key: str, target: str) -> str | None:
    """Se target for @handle, resolve para channel_id via forHandle. Se ja for UC..., devolve direto."""
    if not target.startswith("@"):
        return target
    try:
        resp = requests.get(
            f"{YOUTUBE_API_BASE}/channels",
            params={"part": "id", "forHandle": target.lstrip("@"), "key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            logger.warning("YouTube: handle %s nao encontrado", target)
            return None
        return items[0]["id"]
    except requests.RequestException as e:
        logger.warning("YouTube: erro ao resolver handle %s: %s", target, e)
        return None


def _youtube_get_uploads_playlist_id(api_key: str, channel_id: str) -> str | None:
    try:
        resp = requests.get(
            f"{YOUTUBE_API_BASE}/channels",
            params={"part": "contentDetails", "id": channel_id, "key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            logger.warning("YouTube: canal %s nao encontrado", channel_id)
            return None
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except (requests.RequestException, KeyError) as e:
        logger.warning("YouTube: erro ao buscar playlist de uploads de %s: %s", channel_id, e)
        return None


def _youtube_get_video_ids(api_key: str, playlist_id: str, limite: int = 250) -> list[str]:
    video_ids: list[str] = []
    page_token = None
    while len(video_ids) < limite:
        try:
            resp = requests.get(
                f"{YOUTUBE_API_BASE}/playlistItems",
                params={
                    "part": "contentDetails",
                    "playlistId": playlist_id,
                    "maxResults": 50,
                    "pageToken": page_token,
                    "key": api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("YouTube: erro ao listar videos da playlist %s: %s", playlist_id, e)
            break
        data = resp.json()
        for item in data.get("items", []):
            video_ids.append(item["contentDetails"]["videoId"])
        page_token = data.get("nextPageToken")
        time.sleep(0.05)
        if not page_token:
            break
    return video_ids[:limite]


def _youtube_get_videos_stats(api_key: str, video_ids: list[str], channel_title: str) -> list[dict]:
    """Busca statistics+snippet em lotes de 50 IDs por chamada."""
    rows = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            resp = requests.get(
                f"{YOUTUBE_API_BASE}/videos",
                params={
                    "part": "statistics,snippet",
                    "id": ",".join(batch),
                    "key": api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("YouTube: erro ao buscar estatisticas do lote %d: %s", i, e)
            continue
        for item in resp.json().get("items", []):
            try:
                stats = item.get("statistics", {})
                snippet = item["snippet"]
                rows.append(
                    {
                        "platform": "youtube",
                        "content_id": item["id"],
                        "title": snippet.get("title", ""),
                        "channel_title": snippet.get("channelTitle", channel_title),
                        "username": "",
                        "subreddit": "",
                        "created_at": snippet.get("publishedAt", ""),
                        "likes": stats.get("likeCount", 0),
                        "comments": stats.get("commentCount", 0),
                        "views": stats.get("viewCount", 0),
                    }
                )
            except KeyError as e:
                logger.warning("YouTube: video com campo faltando, pulando: %s", e)
        time.sleep(0.05)
    return rows


def coletar_youtube(api_key: str, targets: list[str]) -> list[dict]:
    """Coleta estatisticas de videos de uma lista de canais (ID, URL ou handle)."""
    all_rows: list[dict] = []
    for raw_target in targets:
        normalized = normalizar_youtube_target(raw_target)
        channel_id = _youtube_resolve_channel_id(api_key, normalized)
        if not channel_id:
            continue
        playlist_id = _youtube_get_uploads_playlist_id(api_key, channel_id)
        if not playlist_id:
            continue
        video_ids = _youtube_get_video_ids(api_key, playlist_id)
        logger.info("YouTube: %d videos encontrados para %s", len(video_ids), raw_target)
        rows = _youtube_get_videos_stats(api_key, video_ids, channel_title=raw_target)
        all_rows.extend(rows)
        logger.info("YouTube: %d linhas coletadas de %s (total ate agora: %d)", len(rows), raw_target, len(all_rows))
    return all_rows


def coletar_reddit(targets: list[str], posts_por_sub: int = 60) -> list[dict]:
    """Coleta posts 'hot' de uma lista de subreddits via endpoint publico (sem OAuth)."""
    all_rows: list[dict] = []
    headers = {"User-Agent": REDDIT_USER_AGENT}
    for raw_target in targets:
        sub = normalizar_reddit_target(raw_target)
        collected = 0
        after = None
        while collected < posts_por_sub:
            try:
                resp = requests.get(
                    f"https://www.reddit.com/r/{sub}/hot.json",
                    params={"limit": 100, "after": after},
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.warning("Reddit: erro ao buscar r/%s: %s", sub, e)
                break
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            if not children:
                break
            for child in children:
                post = child.get("data", {})
                try:
                    created_iso = datetime.fromtimestamp(
                        post["created_utc"], tz=timezone.utc
                    ).isoformat()
                    all_rows.append(
                        {
                            "platform": "reddit",
                            "content_id": post["id"],
                            "title": post.get("title", ""),
                            "channel_title": "",
                            "username": post.get("author", ""),
                            "subreddit": sub,
                            "created_at": created_iso,
                            "likes": post.get("score", 0),
                            "comments": post.get("num_comments", 0),
                            "views": 0,
                        }
                    )
                    collected += 1
                except (KeyError, TypeError, ValueError) as e:
                    logger.warning("Reddit: post malformado em r/%s, pulando: %s", sub, e)
                if collected >= posts_por_sub:
                    break
            after = data.get("data", {}).get("after")
            if not after:
                break
            time.sleep(1.1)  # ~54 req/min, abaixo do limite de 60/min
        logger.info("Reddit: %d posts coletados de r/%s", collected, sub)
    return all_rows
