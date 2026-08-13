"""
Testes do extractor do YouTube: parsing de erro, retry, paginacao e checkpoint.
Rodar com: pytest test_youtube_extractor.py -v
"""

import json
import os
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

import youtube_extractor as ye


class FakeResp:
    def __init__(self, status):
        self.status = status
        self.reason = ""


def make_http_error(status: int, body: dict, uri: str | None = None) -> HttpError:
    # Ensure error object has a "message" field for proper HttpError parsing
    if "error" in body and "message" not in body["error"]:
        body["error"]["message"] = ""
    content = json.dumps(body).encode("utf-8")
    return HttpError(FakeResp(status), content, uri=uri)


# --------------------------------------------------------------------------
# _get_error_reason
# --------------------------------------------------------------------------

def test_get_error_reason_com_lista_de_erros():
    e = make_http_error(403, {"error": {"code": 403, "errors": [{"reason": "commentsDisabled", "message": "x"}]}})
    assert ye._get_error_reason(e) == "commentsDisabled"


def test_get_error_reason_com_message_string_nao_quebra():
    # corpo de erro so com "message" (sem "errors"/"details") -> error_details vira string
    e = make_http_error(500, {"error": {"code": 500, "message": "Backend Error"}})
    assert ye._get_error_reason(e) == ""


def test_get_error_reason_quota_exceeded():
    e = make_http_error(403, {"error": {"code": 403, "errors": [{"reason": "quotaExceeded", "message": "x"}]}})
    assert ye._get_error_reason(e) == "quotaExceeded"


# --------------------------------------------------------------------------
# _execute_with_retry
# --------------------------------------------------------------------------

def test_execute_with_retry_sucesso_primeira_tentativa():
    request = MagicMock()
    request.execute.return_value = {"ok": True}
    assert ye._execute_with_retry(request) == {"ok": True}
    assert request.execute.call_count == 1


def test_execute_with_retry_erro_transitorio_depois_sucesso(monkeypatch):
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)
    transient = make_http_error(500, {"error": {"code": 500, "message": "Backend Error"}})
    request = MagicMock()
    request.execute.side_effect = [transient, {"ok": True}]
    assert ye._execute_with_retry(request, max_retries=3) == {"ok": True}
    assert request.execute.call_count == 2


def test_execute_with_retry_esgota_tentativas_e_propaga(monkeypatch):
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)
    transient = make_http_error(500, {"error": {"code": 500, "message": "Backend Error"}})
    request = MagicMock()
    request.execute.side_effect = [transient, transient, transient]
    with pytest.raises(HttpError):
        ye._execute_with_retry(request, max_retries=3)
    assert request.execute.call_count == 3


def test_execute_with_retry_nao_reintenta_comments_disabled(monkeypatch):
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)
    err = make_http_error(403, {"error": {"code": 403, "errors": [{"reason": "commentsDisabled", "message": "x"}]}})
    request = MagicMock()
    request.execute.side_effect = [err]
    with pytest.raises(HttpError):
        ye._execute_with_retry(request, max_retries=3)
    assert request.execute.call_count == 1


def test_execute_with_retry_nao_reintenta_quota_exceeded(monkeypatch):
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)
    err = make_http_error(403, {"error": {"code": 403, "errors": [{"reason": "quotaExceeded", "message": "x"}]}})
    request = MagicMock()
    request.execute.side_effect = [err]
    with pytest.raises(HttpError):
        ye._execute_with_retry(request, max_retries=3)
    assert request.execute.call_count == 1


# --------------------------------------------------------------------------
# get_uploads_playlist_id / get_all_video_ids usam _execute_with_retry
# --------------------------------------------------------------------------

def test_get_uploads_playlist_id():
    youtube = MagicMock()
    youtube.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "PL123"}}}]
    }
    assert ye.get_uploads_playlist_id(youtube, "UCabc") == "PL123"


def test_get_uploads_playlist_id_canal_inexistente():
    youtube = MagicMock()
    youtube.channels.return_value.list.return_value.execute.return_value = {"items": []}
    with pytest.raises(ValueError):
        ye.get_uploads_playlist_id(youtube, "UCabc")


def test_get_all_video_ids_pagina_duas_vezes():
    youtube = MagicMock()
    page1 = {
        "items": [{"contentDetails": {"videoId": "v1"}, "snippet": {"title": "T1", "videoPublishedAt": "2024-01-01"}}],
        "nextPageToken": "tok2",
    }
    page2 = {
        "items": [{"contentDetails": {"videoId": "v2"}, "snippet": {"title": "T2", "videoPublishedAt": "2024-01-02"}}],
    }
    youtube.playlistItems.return_value.list.return_value.execute.side_effect = [page1, page2]
    videos = ye.get_all_video_ids(youtube, "PL123")
    assert [v["video_id"] for v in videos] == ["v1", "v2"]


# --------------------------------------------------------------------------
# get_comments_for_video
# --------------------------------------------------------------------------

def test_get_comments_for_video_comments_disabled_retorna_vazio():
    youtube = MagicMock()
    err = make_http_error(403, {"error": {"code": 403, "errors": [{"reason": "commentsDisabled", "message": "x"}]}})
    youtube.commentThreads.return_value.list.return_value.execute.side_effect = err
    assert ye.get_comments_for_video(youtube, "v1") == []


def test_get_comments_for_video_quota_exceeded_propaga():
    youtube = MagicMock()
    err = make_http_error(403, {"error": {"code": 403, "errors": [{"reason": "quotaExceeded", "message": "x"}]}})
    youtube.commentThreads.return_value.list.return_value.execute.side_effect = err
    with pytest.raises(HttpError):
        ye.get_comments_for_video(youtube, "v1")


def test_get_comments_for_video_erro_transitorio_esgota_retry_retorna_parcial(monkeypatch):
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)
    transient = make_http_error(500, {"error": {"code": 500, "message": "Backend Error"}})
    youtube = MagicMock()
    youtube.commentThreads.return_value.list.return_value.execute.side_effect = [transient, transient, transient]
    assert ye.get_comments_for_video(youtube, "v1") == []


def test_get_comments_for_video_extrai_top_level_e_replies():
    youtube = MagicMock()
    youtube.commentThreads.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "snippet": {
                    "topLevelComment": {
                        "id": "c1",
                        "snippet": {"authorDisplayName": "A", "textDisplay": "oi", "likeCount": 1, "publishedAt": "2024-01-01"},
                    }
                },
                "replies": {
                    "comments": [
                        {"id": "r1", "snippet": {"authorDisplayName": "B", "textDisplay": "resp", "likeCount": 0, "publishedAt": "2024-01-02"}}
                    ]
                },
            }
        ]
    }
    comments = ye.get_comments_for_video(youtube, "v1")
    assert [c["comment_id"] for c in comments] == ["c1", "r1"]
    assert comments[1]["parent_id"] == "c1"


# --------------------------------------------------------------------------
# checkpoint / resume
# --------------------------------------------------------------------------

def test_checkpoint_progress_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ye, "CHECKPOINT_PATH", str(tmp_path / "checkpoint.jsonl"))
    monkeypatch.setattr(ye, "PROGRESS_PATH", str(tmp_path / "progress.json"))

    assert ye._load_progress() == set()
    ye._save_progress({"v1", "v2"})
    assert ye._load_progress() == {"v1", "v2"}

    ye._append_checkpoint([{"comment_id": "c1"}])
    ye._append_checkpoint([{"comment_id": "c2"}])
    assert ye._load_checkpoint_comments() == [{"comment_id": "c1"}, {"comment_id": "c2"}]

    ye._clear_checkpoint()
    assert ye._load_progress() == set()
    assert ye._load_checkpoint_comments() == []


def test_extract_channel_comments_resume_pula_videos_ja_processados(tmp_path, monkeypatch):
    monkeypatch.setattr(ye, "CHECKPOINT_PATH", str(tmp_path / "checkpoint.jsonl"))
    monkeypatch.setattr(ye, "PROGRESS_PATH", str(tmp_path / "progress.json"))
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)

    # video v1 ja no checkpoint de uma run anterior
    ye._append_checkpoint([{"comment_id": "c1", "video_id": "v1", "video_title": "T1"}])
    ye._save_progress({"v1"})

    monkeypatch.setattr(ye, "get_youtube_client", lambda api_key: MagicMock())
    monkeypatch.setattr(ye, "get_uploads_playlist_id", lambda youtube, channel_id: "PL1")
    monkeypatch.setattr(
        ye,
        "get_all_video_ids",
        lambda youtube, playlist_id: [
            {"video_id": "v1", "title": "T1", "published_at": "2024-01-01"},
            {"video_id": "v2", "title": "T2", "published_at": "2024-01-02"},
        ],
    )
    monkeypatch.setattr(ye, "get_comments_for_video", lambda youtube, video_id: [{"comment_id": "c2", "video_id": video_id}])

    output_path = str(tmp_path / "comentarios_brutos.json")
    result = ye.extract_channel_comments("fake_key", "UCabc", output_path)

    assert {c["comment_id"] for c in result} == {"c1", "c2"}
    # checkpoint limpo apos sucesso
    assert not os.path.exists(ye.CHECKPOINT_PATH)
    assert not os.path.exists(ye.PROGRESS_PATH)
    with open(output_path, encoding="utf-8") as f:
        saved = json.load(f)
    assert {c["comment_id"] for c in saved} == {"c1", "c2"}


def test_extract_channel_comments_resume_apos_crash_entre_checkpoint_e_progress_nao_duplica(tmp_path, monkeypatch):
    monkeypatch.setattr(ye, "CHECKPOINT_PATH", str(tmp_path / "checkpoint.jsonl"))
    monkeypatch.setattr(ye, "PROGRESS_PATH", str(tmp_path / "progress.json"))
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)

    # simula crash entre _append_checkpoint e _save_progress: v1 ja esta no checkpoint,
    # mas progress.json nunca foi escrito (nao existe).
    ye._append_checkpoint([{"comment_id": "c1", "video_id": "v1", "video_title": "T1"}])
    assert not os.path.exists(ye.PROGRESS_PATH)

    monkeypatch.setattr(ye, "get_youtube_client", lambda api_key: MagicMock())
    monkeypatch.setattr(ye, "get_uploads_playlist_id", lambda youtube, channel_id: "PL1")
    monkeypatch.setattr(
        ye,
        "get_all_video_ids",
        lambda youtube, playlist_id: [
            {"video_id": "v1", "title": "T1", "published_at": "2024-01-01"},
            {"video_id": "v2", "title": "T2", "published_at": "2024-01-02"},
        ],
    )
    calls = []

    def fake_get_comments_for_video(youtube, video_id):
        calls.append(video_id)
        return [{"comment_id": f"c_{video_id}", "video_id": video_id}]

    monkeypatch.setattr(ye, "get_comments_for_video", fake_get_comments_for_video)

    output_path = str(tmp_path / "comentarios_brutos.json")
    result = ye.extract_channel_comments("fake_key", "UCabc", output_path)

    # v1 nao deve ser reprocessado (ja estava no checkpoint), so v2 e buscado de novo
    assert calls == ["v2"]
    ids = [c["comment_id"] for c in result]
    assert ids.count("c1") == 1
    assert set(ids) == {"c1", "c_v2"}


def test_load_progress_arquivo_corrompido_retorna_vazio(tmp_path, monkeypatch):
    monkeypatch.setattr(ye, "PROGRESS_PATH", str(tmp_path / "progress.json"))
    with open(ye.PROGRESS_PATH, "w", encoding="utf-8") as f:
        f.write("{isso nao e json valido")

    assert ye._load_progress() == set()


def test_load_checkpoint_comments_linha_final_corrompida_retorna_validas(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ye, "CHECKPOINT_PATH", str(tmp_path / "checkpoint.jsonl"))
    with open(ye.CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps({"comment_id": "c1"}) + "\n")
        f.write(json.dumps({"comment_id": "c2"}) + "\n")
        # linha final truncada, simulando crash no meio do write
        f.write('{"comment_id": "c3", "text": "trunc')

    comments = ye._load_checkpoint_comments()

    assert comments == [{"comment_id": "c1"}, {"comment_id": "c2"}]
    captured = capsys.readouterr()
    assert "aviso" in captured.out


# --------------------------------------------------------------------------
# _safe_error_str: nao vazar API key (query string da uri) nos logs
# --------------------------------------------------------------------------

def test_safe_error_str_nao_contem_uri():
    e = make_http_error(
        500,
        {"error": {"code": 500, "message": "Backend Error"}},
        uri="https://www.googleapis.com/youtube/v3/commentThreads?key=FAKE_SECRET_KEY_12345",
    )
    result = ye._safe_error_str(e)
    assert "FAKE_SECRET_KEY_12345" not in result
    assert "HTTP 500" in result


def test_execute_with_retry_nao_vaza_api_key_no_stdout(monkeypatch, capsys):
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)
    transient = make_http_error(
        500,
        {"error": {"code": 500, "message": "Backend Error"}},
        uri="https://www.googleapis.com/youtube/v3/commentThreads?key=FAKE_SECRET_KEY_12345",
    )
    request = MagicMock()
    request.execute.side_effect = [transient, {"ok": True}]

    ye._execute_with_retry(request, max_retries=3)

    captured = capsys.readouterr()
    assert "FAKE_SECRET_KEY_12345" not in captured.out


def test_get_comments_for_video_nao_vaza_api_key_no_stdout(monkeypatch, capsys):
    monkeypatch.setattr(ye.time, "sleep", lambda s: None)
    transient = make_http_error(
        500,
        {"error": {"code": 500, "message": "Backend Error"}},
        uri="https://www.googleapis.com/youtube/v3/commentThreads?key=FAKE_SECRET_KEY_12345",
    )
    youtube = MagicMock()
    youtube.commentThreads.return_value.list.return_value.execute.side_effect = [transient, transient, transient]

    result = ye.get_comments_for_video(youtube, "v1")

    assert result == []
    captured = capsys.readouterr()
    assert "FAKE_SECRET_KEY_12345" not in captured.out
