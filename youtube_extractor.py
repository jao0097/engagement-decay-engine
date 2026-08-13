"""
Extrai todos os comentarios (e respostas) de todos os videos de um canal do YouTube,
usando a YouTube Data API v3.

Estrategia de cota:
- Usa playlistItems.list (1 unidade) para listar videos, em vez de search.list (100 unidades).
- commentThreads.list custa 1 unidade por chamada, com paginacao de 100 comentarios por pagina.
"""

import json
import os
import time

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

CHECKPOINT_PATH = "./data/comentarios_brutos.checkpoint.jsonl"
PROGRESS_PATH = "./data/comentarios_brutos.progress.json"


def _load_progress() -> set[str]:
    if not os.path.exists(PROGRESS_PATH):
        return set()
    try:
        with open(PROGRESS_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [aviso] progresso corrompido, ignorando: {e}")
        return set()


def _save_progress(done_video_ids: set[str]) -> None:
    os.makedirs(os.path.dirname(PROGRESS_PATH), exist_ok=True)
    tmp_path = PROGRESS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sorted(done_video_ids), f)
    os.replace(tmp_path, PROGRESS_PATH)


def _append_checkpoint(video_comments: list[dict]) -> None:
    os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
    with open(CHECKPOINT_PATH, "a", encoding="utf-8") as f:
        for c in video_comments:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def _load_checkpoint_comments() -> list[dict]:
    if not os.path.exists(CHECKPOINT_PATH):
        return []
    comments = []
    with open(CHECKPOINT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                comments.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  [aviso] linha de checkpoint corrompida, ignorando: {e}")
    return comments


def _clear_checkpoint() -> None:
    for path in (CHECKPOINT_PATH, PROGRESS_PATH):
        if os.path.exists(path):
            os.remove(path)


def _get_error_reason(e: HttpError) -> str:
    """Extrai o 'reason' do erro da API, tratando error_details como lista OU string."""
    details = e.error_details
    if isinstance(details, list) and details and isinstance(details[0], dict):
        return details[0].get("reason", "")
    return ""


def _safe_error_str(e: HttpError) -> str:
    """
    Representacao segura do erro para logs, sem a URI/query string da request
    (que inclui a API key via developerKey=...). Nunca formatar o HttpError cru.
    """
    reason = _get_error_reason(e)
    status = getattr(e, "status_code", None) or getattr(getattr(e, "resp", None), "status", "?")
    return f"HTTP {status}" + (f" ({reason})" if reason else "")


def _execute_with_retry(request, max_retries: int = 3):
    """
    Executa uma request da API do YouTube com retry curto em erros transitorios.
    commentsDisabled e quotaExceeded nao sao retentados (nao adianta tentar de novo).
    """
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            reason = _get_error_reason(e)
            if reason in ("commentsDisabled", "quotaExceeded"):
                raise
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  [retry] erro transitorio ({_safe_error_str(e)}), tentativa {attempt + 1}/{max_retries}, aguardando {wait}s...")
            time.sleep(wait)


def get_youtube_client(api_key: str):
    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, developerKey=api_key)


def get_uploads_playlist_id(youtube, channel_id: str) -> str:
    """Pega o ID da playlist 'uploads' do canal (todos os videos publicados)."""
    response = _execute_with_retry(youtube.channels().list(part="contentDetails", id=channel_id))
    items = response.get("items", [])
    if not items:
        raise ValueError(f"Canal nao encontrado: {channel_id}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def get_all_video_ids(youtube, playlist_id: str) -> list[dict]:
    """Retorna lista de {video_id, title, published_at} de todos os videos da playlist."""
    videos = []
    page_token = None

    while True:
        response = _execute_with_retry(
            youtube.playlistItems().list(
                part="contentDetails,snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
        )
        for item in response.get("items", []):
            videos.append(
                {
                    "video_id": item["contentDetails"]["videoId"],
                    "title": item["snippet"]["title"],
                    "published_at": item["contentDetails"].get("videoPublishedAt", ""),
                }
            )
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return videos


def get_comments_for_video(youtube, video_id: str) -> list[dict]:
    """
    Retorna todos os comentarios (top-level + respostas) de um video.
    Se os comentarios estiverem desabilitados, retorna lista vazia em vez de quebrar.
    """
    comments = []
    page_token = None

    while True:
        try:
            response = _execute_with_retry(
                youtube.commentThreads().list(
                    part="snippet,replies",
                    videoId=video_id,
                    maxResults=100,
                    pageToken=page_token,
                    textFormat="plainText",
                )
            )
        except HttpError as e:
            reason = _get_error_reason(e)
            if reason == "commentsDisabled":
                return []
            if reason == "quotaExceeded":
                raise
            print(f"  [aviso] erro ao buscar comentarios do video {video_id} apos retries: {_safe_error_str(e)}")
            return comments

        for thread in response.get("items", []):
            top = thread["snippet"]["topLevelComment"]["snippet"]
            comments.append(
                {
                    "comment_id": thread["snippet"]["topLevelComment"]["id"],
                    "video_id": video_id,
                    "parent_id": None,
                    "author": top.get("authorDisplayName", ""),
                    "text": top.get("textDisplay", ""),
                    "like_count": top.get("likeCount", 0),
                    "published_at": top.get("publishedAt", ""),
                }
            )

            for reply in thread.get("replies", {}).get("comments", []):
                r = reply["snippet"]
                comments.append(
                    {
                        "comment_id": reply["id"],
                        "video_id": video_id,
                        "parent_id": thread["snippet"]["topLevelComment"]["id"],
                        "author": r.get("authorDisplayName", ""),
                        "text": r.get("textDisplay", ""),
                        "like_count": r.get("likeCount", 0),
                        "published_at": r.get("publishedAt", ""),
                    }
                )

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return comments


def extract_channel_comments(api_key: str, channel_id: str, output_path: str) -> list[dict]:
    """Fluxo completo: lista videos do canal, extrai comentarios de cada um, salva em JSON."""
    youtube = get_youtube_client(api_key)

    print(f"Buscando playlist de uploads do canal {channel_id}...")
    playlist_id = get_uploads_playlist_id(youtube, channel_id)

    print("Listando videos...")
    videos = get_all_video_ids(youtube, playlist_id)
    print(f"  {len(videos)} videos encontrados.")

    done_video_ids = _load_progress()
    all_comments = _load_checkpoint_comments()
    if all_comments:
        done_video_ids |= {c["video_id"] for c in all_comments}
    if done_video_ids:
        print(f"Checkpoint encontrado: {len(done_video_ids)} videos ja processados, retomando.")

    for i, video in enumerate(videos, start=1):
        if video["video_id"] in done_video_ids:
            continue
        print(f"[{i}/{len(videos)}] Extraindo comentarios de: {video['title'][:60]}")
        video_comments = get_comments_for_video(youtube, video["video_id"])
        for c in video_comments:
            c["video_title"] = video["title"]
        _append_checkpoint(video_comments)
        all_comments.extend(video_comments)
        done_video_ids.add(video["video_id"])
        _save_progress(done_video_ids)
        time.sleep(0.05)  # pequena folga para nao martelar a API

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_comments, f, ensure_ascii=False, indent=2)

    _clear_checkpoint()

    print(f"\nTotal de comentarios extraidos: {len(all_comments)}")
    print(f"Salvo em: {output_path}")
    return all_comments
