"""
Testes do extractor do YouTube: parsing de erro, retry, paginacao e checkpoint.
Rodar com: pytest test_youtube_extractor.py -v
"""

import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

import youtube_extractor as ye


class FakeResp:
    def __init__(self, status):
        self.status = status
        self.reason = ""


def make_http_error(status: int, body: dict) -> HttpError:
    # Ensure error object has a "message" field for proper HttpError parsing
    if "error" in body and "message" not in body["error"]:
        body["error"]["message"] = ""
    content = json.dumps(body).encode("utf-8")
    return HttpError(FakeResp(status), content)


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
