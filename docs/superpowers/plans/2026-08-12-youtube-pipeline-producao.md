# YouTube Pipeline Produção Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Torna o pipeline YouTube (extração → classificação → storage) resiliente a crashes,
evita reprocessamento desnecessário, corrige um bug de crash em erro HTTP não padrão, e cobre
`youtube_extractor.py`/`classifier.py`/`storage.py` com testes mockados.

**Architecture:** Checkpoint append-only (JSONL) para extração resumível por vídeo; retry curto
em erros transitórios da API do YouTube; skip-guard + flag `--reclassify` em `main.py`;
validação de categorias e `max_tokens` explícito na classificação Groq; singleton de embedding
model no storage. Sem mudança de formato dos arquivos finais (`comentarios_brutos.json`,
`comentarios_classificados.json`) — tudo compatível com o pipeline existente.

**Tech Stack:** Python 3, `google-api-python-client`, `groq`, `chromadb`, `pytest`,
`unittest.mock`.

## Global Constraints

- Escopo restrito a `youtube_extractor.py`, `classifier.py`, `storage.py`, `main.py`,
  `finish_classification.py` e seus testes. **Não tocar** `decay_engine.py`,
  `scoring_engine.py`, `schema.sql`, `build_engagement_state.py`, `app.py`,
  `test_decay_engine.py`, `test_scoring_engine.py` (outro trabalho em andamento em paralelo).
- Testes não fazem chamada real de rede (YouTube API / Groq API) — tudo mockado com
  `unittest.mock`.
- Sem mudança no formato final de `data/comentarios_brutos.json` ou
  `data/comentarios_classificados.json` — downstream (`classifier.py`, `storage.py`,
  `build_engagement_state.py`) continua lendo o mesmo formato.
- Backoff de retry curto: `2 ** tentativa` segundos, no máximo 3 tentativas (1s, 2s, depois
  desiste — só espera antes da 2ª e 3ª tentativa).
- **Não commitar** sem validação explícita do usuário — ao final de cada task, rodar os testes
  e reportar, mas o `git commit` de cada task só deve rodar depois de aprovação do usuário. (Os
  passos abaixo incluem comando de commit por completude do histórico de trabalho; segurar a
  execução dele até o usuário validar.)

---

### Task 1: `youtube_extractor.py` — fix `error_details` + retry curto

**Files:**
- Modify: `youtube_extractor.py`
- Test: `test_youtube_extractor.py` (novo)

**Interfaces:**
- Produces: `_get_error_reason(e: HttpError) -> str`, `_execute_with_retry(request, max_retries: int = 3)` —
  usados pela Task 2 dentro de `get_comments_for_video`, `get_all_video_ids`,
  `get_uploads_playlist_id`.

- [ ] **Step 1: Escrever testes falhando**

Criar `test_youtube_extractor.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `pytest test_youtube_extractor.py -v`
Expected: FAIL (`AttributeError: module 'youtube_extractor' has no attribute '_get_error_reason'`)

- [ ] **Step 3: Implementar `_get_error_reason` e `_execute_with_retry`**

No topo de `youtube_extractor.py`, logo após os imports existentes, adicionar:

```python
def _get_error_reason(e: HttpError) -> str:
    """Extrai o 'reason' do erro da API, tratando error_details como lista OU string."""
    details = e.error_details
    if isinstance(details, list) and details and isinstance(details[0], dict):
        return details[0].get("reason", "")
    return ""


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
            print(f"  [retry] erro transitorio ({reason or e}), tentativa {attempt + 1}/{max_retries}, aguardando {wait}s...")
            time.sleep(wait)
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest test_youtube_extractor.py -v`
Expected: PASS (todos os 6 testes acima)

- [ ] **Step 5: Commit** (aguardar validação do usuário antes de rodar)

```bash
git add youtube_extractor.py test_youtube_extractor.py
git commit -m "fix: trata error_details como str ou lista e adiciona retry curto na API do YouTube"
```

---

### Task 2: `youtube_extractor.py` — aplica retry nas chamadas + checkpoint/resume

**Files:**
- Modify: `youtube_extractor.py`
- Test: `test_youtube_extractor.py`

**Interfaces:**
- Consumes: `_get_error_reason`, `_execute_with_retry` (Task 1).
- Produces: `_load_progress`, `_save_progress`, `_append_checkpoint`,
  `_load_checkpoint_comments`, `_clear_checkpoint`, constantes `CHECKPOINT_PATH`,
  `PROGRESS_PATH`. `extract_channel_comments` mantém a mesma assinatura e o mesmo formato de
  retorno/arquivo final.

- [ ] **Step 1: Escrever testes falhando para paginação com retry, checkpoint e resume**

Adicionar a `test_youtube_extractor.py`:

```python
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
```

Adicionar `import os` já existe no arquivo de teste? Não — adicionar `import os` no topo de
`test_youtube_extractor.py` junto aos outros imports.

- [ ] **Step 2: Rodar e confirmar falha**

Run: `pytest test_youtube_extractor.py -v`
Expected: FAIL nos testes de checkpoint (`AttributeError: module 'youtube_extractor' has no attribute 'CHECKPOINT_PATH'`) e no de retry transitório de `get_comments_for_video` (ainda não usa `_execute_with_retry`).

- [ ] **Step 3: Aplicar retry nas 3 funções de chamada de API**

Em `get_uploads_playlist_id`, trocar:

```python
    response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
```

por:

```python
    response = _execute_with_retry(youtube.channels().list(part="contentDetails", id=channel_id))
```

Em `get_all_video_ids`, trocar o bloco da request dentro do `while True:` (o `.execute()` no
final da chamada `playlistItems().list(...)`) para usar `_execute_with_retry`:

```python
        response = _execute_with_retry(
            youtube.playlistItems().list(
                part="contentDetails,snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
        )
```

Em `get_comments_for_video`, trocar o bloco `try/except` para usar `_execute_with_retry` e
`_get_error_reason`:

```python
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
            print(f"  [aviso] erro ao buscar comentarios do video {video_id} apos retries: {e}")
            return comments
```

- [ ] **Step 4: Implementar checkpoint/progress e integrar no `extract_channel_comments`**

Adicionar constantes e funções auxiliares (após as constantes `YOUTUBE_API_SERVICE_NAME`/
`YOUTUBE_API_VERSION`):

```python
CHECKPOINT_PATH = "./data/comentarios_brutos.checkpoint.jsonl"
PROGRESS_PATH = "./data/comentarios_brutos.progress.json"


def _load_progress() -> set[str]:
    if not os.path.exists(PROGRESS_PATH):
        return set()
    with open(PROGRESS_PATH, encoding="utf-8") as f:
        return set(json.load(f))


def _save_progress(done_video_ids: set[str]) -> None:
    os.makedirs(os.path.dirname(PROGRESS_PATH), exist_ok=True)
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(done_video_ids), f)


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
            if line:
                comments.append(json.loads(line))
    return comments


def _clear_checkpoint() -> None:
    for path in (CHECKPOINT_PATH, PROGRESS_PATH):
        if os.path.exists(path):
            os.remove(path)
```

Reescrever o corpo de `extract_channel_comments`:

```python
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
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `pytest test_youtube_extractor.py -v`
Expected: PASS (todos os testes do arquivo)

- [ ] **Step 6: Commit** (aguardar validação do usuário antes de rodar)

```bash
git add youtube_extractor.py test_youtube_extractor.py
git commit -m "feat: checkpoint append-only de extracao resumivel por video + retry nas chamadas da API"
```

---

### Task 3: `classifier.py` — validação de categorias, `max_tokens`, dedup de código morto, negação em `quick_classify`

**Files:**
- Modify: `classifier.py`
- Test: `test_classifier.py` (novo)

**Interfaces:**
- Produces: `_validate_categorias(categorias: list[str]) -> list[str]`.
- `quick_classify` mantém assinatura `(text: str) -> list[str] | None`.
- `classify_batch` ganha `max_tokens` explícito na chamada Groq (mesma assinatura pública).

- [ ] **Step 1: Escrever testes falhando**

Criar `test_classifier.py`:

```python
"""
Testes do classificador: regras rapidas, dedup, validacao de categorias e fallback.
Rodar com: pytest test_classifier.py -v
"""

from unittest.mock import MagicMock, patch

import classifier as cl


# --------------------------------------------------------------------------
# quick_classify
# --------------------------------------------------------------------------

def test_quick_classify_emoji_isolado():
    assert cl.quick_classify("👏👏👏") == ["sem_conteudo_classificavel"]


def test_quick_classify_kkk():
    assert cl.quick_classify("kkkkkk") == ["sem_conteudo_classificavel"]


def test_quick_classify_agradecimento_curto():
    assert cl.quick_classify("muito obrigado!") == ["agradecimento"]


def test_quick_classify_agradecimento_com_negacao_vai_para_llm():
    assert cl.quick_classify("não, obrigado") is None


def test_quick_classify_ambiguo_retorna_none():
    assert cl.quick_classify("esse video mudou minha forma de pensar sobre o assunto") is None


# --------------------------------------------------------------------------
# _validate_categorias
# --------------------------------------------------------------------------

def test_validate_categorias_mantem_validas():
    assert cl._validate_categorias(["elogio_generico", "pergunta_duvida"]) == ["elogio_generico", "pergunta_duvida"]


def test_validate_categorias_remove_invalidas():
    assert cl._validate_categorias(["categoria_inventada", "agradecimento"]) == ["agradecimento"]


def test_validate_categorias_todas_invalidas_cai_no_default():
    assert cl._validate_categorias(["nao_existe"]) == ["sem_conteudo_classificavel"]


def test_validate_categorias_lista_vazia_cai_no_default():
    assert cl._validate_categorias([]) == ["sem_conteudo_classificavel"]


# --------------------------------------------------------------------------
# classify_comments: dedup + fallback + checkpoint resume
# --------------------------------------------------------------------------

def test_classify_comments_propaga_resultado_para_grupo_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CHECKPOINT_PATH", str(tmp_path / "checkpoint.json"))
    comments = [
        {"comment_id": "c1", "text": "conteudo unico interessante sobre o tema"},
        {"comment_id": "c2", "text": "conteudo unico interessante sobre o tema"},  # duplicata exata
    ]
    with patch.object(cl, "Groq") as MockGroq:
        client = MagicMock()
        MockGroq.return_value = client
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content='{"resultados": [{"id": "c1", "categorias": ["contribuicao_valor"], "score_engajamento": 0.8}]}'))
        ]
        result = cl.classify_comments(["fake_key"], comments)

    by_id = {c["comment_id"]: c for c in result}
    assert by_id["c1"]["categorias"] == ["contribuicao_valor"]
    assert by_id["c2"]["categorias"] == ["contribuicao_valor"]
    assert by_id["c2"]["score_engajamento"] == 0.8
    # so uma chamada ao LLM, pois c1 e c2 sao duplicatas apos normalizacao
    assert client.chat.completions.create.call_count == 1


def test_classify_comments_categoria_invalida_do_llm_vira_default(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CHECKPOINT_PATH", str(tmp_path / "checkpoint.json"))
    comments = [{"comment_id": "c1", "text": "comentario qualquer bem especifico aqui"}]
    with patch.object(cl, "Groq") as MockGroq:
        client = MagicMock()
        MockGroq.return_value = client
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content='{"resultados": [{"id": "c1", "categorias": ["categoria_alucinada"], "score_engajamento": 0.5}]}'))
        ]
        result = cl.classify_comments(["fake_key"], comments)

    assert result[0]["categorias"] == ["sem_conteudo_classificavel"]


def test_classify_comments_resume_ignora_ja_classificados(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(cl, "CHECKPOINT_PATH", str(checkpoint_path))
    import json
    checkpoint_path.write_text(json.dumps([{"comment_id": "c1", "text": "ja classificado", "categorias": ["agradecimento"], "score_engajamento": 0.1}]))

    comments = [{"comment_id": "c1", "text": "ja classificado"}]
    with patch.object(cl, "Groq") as MockGroq:
        result = cl.classify_comments(["fake_key"], comments)
        MockGroq.assert_not_called()

    assert result[0]["categorias"] == ["agradecimento"]
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `pytest test_classifier.py -v`
Expected: FAIL (`AttributeError: module 'classifier' has no attribute '_validate_categorias'`, e o teste de negação falha pois hoje `quick_classify` classifica "não, obrigado" como agradecimento)

- [ ] **Step 3: Implementar `_validate_categorias` e usar no fluxo de propagação**

Adicionar logo abaixo da constante `CATEGORIES`:

```python
def _validate_categorias(categorias: list[str]) -> list[str]:
    """Mantem so categorias conhecidas; se nenhuma sobrar, cai no default."""
    validas = [c for c in categorias if c in CATEGORIES]
    return validas if validas else ["sem_conteudo_classificavel"]
```

Substituir o bloco de propagação em `classify_comments` (dentro do loop de batches, onde hoje
está o `if result and "score_engajamento" in result: ... else: ...`) por:

```python
        for rep in batch:
            result = results.get(rep["comment_id"])
            norm = normalizar(rep["text"])

            for c in groups[norm]:
                if result and "score_engajamento" in result:
                    c["categorias"] = _validate_categorias(result.get("categorias", []))
                    c["score_engajamento"] = float(result.get("score_engajamento", 0.0))
                elif result:
                    c["categorias"] = _validate_categorias(result.get("categorias", []))
                    c["score_engajamento"] = 0.2
                else:
                    c["categorias"] = ["sem_conteudo_classificavel"]
                    c["score_engajamento"] = 0.0
                classified.append(c)
```

- [ ] **Step 4: `max_tokens` explícito, `BATCH_SIZE` menor e remoção de código morto**

Trocar:

```python
MODEL = "llama-3.1-8b-instant"
BATCH_SIZE = 100  # Aumentando para processar mais por chamada
```

por:

```python
MODEL = "llama-3.1-8b-instant"
BATCH_SIZE = 50  # 100 arriscava truncar a resposta JSON do modelo
MAX_TOKENS_PER_BATCH = 4096
```

Em `classify_batch`, adicionar `max_tokens=MAX_TOKENS_PER_BATCH` na chamada
`client.chat.completions.create(...)`:

```python
            response = client.chat.completions.create(
                model=MODEL,
                response_format={"type": "json_object"},
                max_tokens=MAX_TOKENS_PER_BATCH,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(batch)},
                ],
                temperature=0,
            )
```

Remover a linha morta `final_results = {}` em `classify_comments` (comentário "1. Quick
Classify" continua, só a atribuição não usada some).

- [ ] **Step 5: Negação em `quick_classify`**

Trocar:

```python
    # agradecimento puro e curto, sem pergunta
    if len(limpo.split()) <= 6 and "?" not in limpo:
        if re.search(r"\bobrigad|gratid[aã]o|valeu\b", limpo):
            return ["agradecimento"]
```

por:

```python
    # agradecimento puro e curto, sem pergunta e sem negacao
    if len(limpo.split()) <= 6 and "?" not in limpo:
        tem_negacao = re.search(r"\bn[ãa]o\b", limpo)
        if re.search(r"\bobrigad|gratid[aã]o|valeu\b", limpo) and not tem_negacao:
            return ["agradecimento"]
```

- [ ] **Step 6: Rodar e confirmar sucesso**

Run: `pytest test_classifier.py -v`
Expected: PASS (todos os testes do arquivo)

- [ ] **Step 7: Commit** (aguardar validação do usuário antes de rodar)

```bash
git add classifier.py test_classifier.py
git commit -m "fix: valida categorias do LLM, reduz risco de truncamento e trata negacao no quick_classify"
```

---

### Task 4: `main.py` — skip-guard de classificação + flag `--reclassify`

**Files:**
- Modify: `main.py`
- Test: `test_main.py` (novo)

**Interfaces:**
- Produces: `_parse_args(argv: list[str]) -> tuple[str, bool]` (channel_id, reclassify),
  constante `DEFAULT_CHANNEL_ID`.

- [ ] **Step 1: Escrever testes falhando**

Criar `test_main.py`:

```python
"""
Testes do parsing de argumentos do pipeline principal.
Rodar com: pytest test_main.py -v
"""

import main as m


def test_parse_args_sem_argumentos_usa_canal_default():
    channel_id, reclassify = m._parse_args([])
    assert channel_id == m.DEFAULT_CHANNEL_ID
    assert reclassify is False


def test_parse_args_com_canal_explicito():
    channel_id, reclassify = m._parse_args(["UCabc123"])
    assert channel_id == "UCabc123"
    assert reclassify is False


def test_parse_args_com_reclassify():
    channel_id, reclassify = m._parse_args(["UCabc123", "--reclassify"])
    assert channel_id == "UCabc123"
    assert reclassify is True


def test_parse_args_reclassify_sem_canal_usa_default():
    channel_id, reclassify = m._parse_args(["--reclassify"])
    assert channel_id == m.DEFAULT_CHANNEL_ID
    assert reclassify is True
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `pytest test_main.py -v`
Expected: FAIL (`AttributeError: module 'main' has no attribute '_parse_args'`)

- [ ] **Step 3: Implementar `_parse_args`, `DEFAULT_CHANNEL_ID` e o skip-guard**

Trocar o topo de `main()`:

```python
def main():
    if len(sys.argv) < 2:
        channel_id = "UC1Nm7gQCcGvgLyVcGTXp-Ww"
        print(f"Nenhum CHANNEL_ID especificado. Usando o canal padrão do sistema: {channel_id}")
    else:
        channel_id = sys.argv[1]
```

por, adicionando `DEFAULT_CHANNEL_ID` como constante de módulo (junto de
`RAW_COMMENTS_PATH`/`CLASSIFIED_COMMENTS_PATH`) e uma função `_parse_args`:

```python
DEFAULT_CHANNEL_ID = "UC1Nm7gQCcGvgLyVcGTXp-Ww"


def _parse_args(argv: list[str]) -> tuple[str, bool]:
    """Retorna (channel_id, reclassify) a partir de sys.argv[1:]."""
    reclassify = "--reclassify" in argv
    positional = [a for a in argv if not a.startswith("--")]
    channel_id = positional[0] if positional else DEFAULT_CHANNEL_ID
    return channel_id, reclassify


def main():
    channel_id, reclassify = _parse_args(sys.argv[1:])
    if channel_id == DEFAULT_CHANNEL_ID:
        print(f"Nenhum CHANNEL_ID especificado. Usando o canal padrão do sistema: {channel_id}")
```

Trocar a Etapa 2 (bloco de classificação) de:

```python
    # Etapa 2: classificacao
    print("\n=== Classificando comentarios ===")
    classified = classify_comments(groq_api_keys, comments)

    os.makedirs(os.path.dirname(CLASSIFIED_COMMENTS_PATH), exist_ok=True)
    with open(CLASSIFIED_COMMENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)
    print(f"Classificacao salva em: {CLASSIFIED_COMMENTS_PATH}")
```

por:

```python
    # Etapa 2: classificacao (pula se ja existe, a menos que --reclassify seja passado)
    if os.path.exists(CLASSIFIED_COMMENTS_PATH) and not reclassify:
        print(f"\nArquivo {CLASSIFIED_COMMENTS_PATH} ja existe, pulando classificacao.")
        print("(passe --reclassify para forcar reclassificacao)\n")
        with open(CLASSIFIED_COMMENTS_PATH, encoding="utf-8") as f:
            classified = json.load(f)
    else:
        print("\n=== Classificando comentarios ===")
        classified = classify_comments(groq_api_keys, comments)

        os.makedirs(os.path.dirname(CLASSIFIED_COMMENTS_PATH), exist_ok=True)
        with open(CLASSIFIED_COMMENTS_PATH, "w", encoding="utf-8") as f:
            json.dump(classified, f, ensure_ascii=False, indent=2)
        print(f"Classificacao salva em: {CLASSIFIED_COMMENTS_PATH}")
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest test_main.py -v`
Expected: PASS (todos os 4 testes)

- [ ] **Step 5: Commit** (aguardar validação do usuário antes de rodar)

```bash
git add main.py test_main.py
git commit -m "feat: skip-guard na classificacao com flag --reclassify para forcar"
```

---

### Task 5: `storage.py` — singleton de embedding model

**Files:**
- Modify: `storage.py`
- Test: `test_storage.py` (novo)

**Interfaces:**
- `get_collection()` mantém assinatura `() -> Collection`. Nenhuma mudança de interface pública.

- [ ] **Step 1: Escrever testes falhando**

Criar `test_storage.py`:

```python
"""
Testes de storage: metadata montada e singleton do embedding model.
Rodar com: pytest test_storage.py -v
"""

from unittest.mock import MagicMock, patch

import storage as st


def setup_function():
    st._collection = None


def test_get_collection_reusa_instancia_entre_chamadas():
    with patch.object(st.chromadb, "PersistentClient") as MockClient, \
         patch.object(st.embedding_functions, "SentenceTransformerEmbeddingFunction") as MockEmbedFn:
        MockClient.return_value.get_or_create_collection.return_value = MagicMock()

        c1 = st.get_collection()
        c2 = st.get_collection()

        assert c1 is c2
        assert MockClient.call_count == 1
        assert MockEmbedFn.call_count == 1


def test_store_comments_monta_metadata_correta():
    comments = [
        {
            "comment_id": "c1",
            "video_id": "v1",
            "video_title": "Titulo",
            "author": "Fulano",
            "text": "texto do comentario",
            "like_count": 3,
            "published_at": "2024-01-01",
            "parent_id": "c0",
            "categorias": ["agradecimento", "elogio_generico"],
            "score_engajamento": 0.3,
        }
    ]
    fake_collection = MagicMock()
    fake_collection.count.return_value = 1
    with patch.object(st, "get_collection", return_value=fake_collection):
        st.store_comments(comments)

    _, kwargs = fake_collection.upsert.call_args
    assert kwargs["ids"] == ["c1"]
    assert kwargs["metadatas"][0]["categorias"] == "agradecimento,elogio_generico"
    assert kwargs["metadatas"][0]["is_reply"] is True
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `pytest test_storage.py -v`
Expected: FAIL no teste de singleton (`assert MockClient.call_count == 1` falha, hoje é 2 —
uma por chamada de `get_collection()`)

- [ ] **Step 3: Implementar o singleton**

Trocar:

```python
def get_collection():
    client = chromadb.PersistentClient(path=DB_PATH)
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME, embedding_function=embedding_fn
    )
```

por:

```python
_collection = None


def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=DB_PATH)
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME, embedding_function=embedding_fn
        )
    return _collection
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest test_storage.py -v`
Expected: PASS (todos os testes do arquivo)

- [ ] **Step 5: Commit** (aguardar validação do usuário antes de rodar)

```bash
git add storage.py test_storage.py
git commit -m "perf: cacheia embedding model em singleton, evita recarregar a cada chamada"
```

---

### Task 6: Suite completa + validação final

**Files:**
- Nenhum arquivo novo — só validação.

- [ ] **Step 1: Rodar toda a suite nova + a existente, garantir que nada quebrou**

Run: `pytest test_youtube_extractor.py test_classifier.py test_main.py test_storage.py test_scoring_engine.py test_decay_engine.py -v`
Expected: PASS em tudo (os dois últimos arquivos não devem ter sido tocados, só confirma que
nada neste trabalho colidiu com o engine de decay que está sendo mexido em paralelo).

- [ ] **Step 2: Reportar ao usuário e pedir validação para os commits pendentes**

Nenhum commit deste plano deve ter sido efetivamente executado antes desta etapa (ver Global
Constraints). Apresentar o diff resumido de cada task e pedir aprovação explícita antes de
rodar os `git commit` das Tasks 1–5.

---

## Self-Review

**Cobertura do spec:**
- §1 checkpoint de extração → Task 2.
- §2 retry + fix `error_details` → Task 1 (fix + helper) e Task 2 (aplicação nas 3 funções).
- §3 skip-guard, validação de categorias, `max_tokens`/`BATCH_SIZE`, código morto, negação →
  Tasks 3 e 4.
- §4 singleton de embedding → Task 5.
- §5 testes mockados para os três módulos → Tasks 1, 2, 3, 4, 5 (testes embutidos em cada task
  via TDD, não uma task separada).
- "Fora de escopo" (decay engine, incremental entre runs, cron/logging) → nenhuma task toca
  nesses arquivos/funcionalidades.
- "Commits" (sem commit sem validação) → reforçado em Global Constraints e na Task 6.

**Consistência de tipos:** `_get_error_reason(e) -> str`, `_execute_with_retry(request,
max_retries=3)`, `_validate_categorias(categorias: list[str]) -> list[str]`, `_parse_args(argv:
list[str]) -> tuple[str, bool]` usados de forma consistente entre a task que define e as tasks
seguintes que os consomem (Task 1 → Task 2; Task 3 interna; Task 4 interna).
