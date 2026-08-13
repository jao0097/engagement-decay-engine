# Régua de Engajamento Universal (YouTube + WhatsApp) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o motor de decaimento de engajamento (`decay_engine.py`) ingerir eventos de qualquer plataforma através de um schema universal, adicionando um adaptador WhatsApp (export manual `.txt`, scoring local via `HeuristicScorer`) ao lado do adaptador YouTube já existente, com `base_weight` calibrável por plataforma.

**Architecture:** Cada plataforma produz eventos num schema universal comum (`event_id, platform, author_id, author_display_name, content_id, published_at, quality_score, categorias`). Uma função tradutora (`to_decay_engine_events`) namespacia o autor como `"{platform}:{author_id}"` antes de entrar no motor — isso evita colisão entre plataformas sem precisar de chave composta em pandas, e o motor de decaimento em si (`decay`, `apply_hysteresis`, `backfill_history`) não muda uma linha de matemática. `build_engagement_state.py` vira um laço por plataforma: cada uma tem seu próprio cutoff incremental e seu próprio `base_weight`.

**Tech Stack:** Python 3.10+, pandas, numpy, sqlite3 (stdlib), pytest. Sem novas dependências.

## Global Constraints

- Sem identidade cross-platform: o mesmo humano em duas redes vira dois autores distintos, sem tentativa de matching.
- Sem mecanismo antispam/anti-rajada nesta fase (comunidades pequenas, não é prioridade agora).
- WhatsApp só via export manual `.txt` (formato Android: `DD/MM/AAAA HH:MM - Autor: mensagem`); formato iOS fora de escopo.
- `decay_engine.py`: proibido laço Python linha-a-linha sobre eventos — o único laço permitido é sobre dias-com-evento em `backfill_history` (constraint já existente do projeto, não muda com este plano).
- `engagement.db` é gitignorado e local — a mudança de schema é breaking sem migração; documentar que requer `--rebuild` uma vez.
- Scoring do WhatsApp usa só `HeuristicScorer` (sem chamada de LLM).
- Spec: `docs/superpowers/specs/2026-08-12-omnichannel-engagement-ruler-design.md`.

---

### Task 1: `schema.sql` — coluna `platform` e nomes genéricos de coluna

**Files:**
- Modify: `schema.sql`
- Test: `test_decay_engine.py` (adiciona teste no topo do arquivo)

**Interfaces:**
- Consumes: nada.
- Produces: schema SQLite com `authors.platform`, `engagement_events.platform`, `engagement_events.content_id` (era `video_id`), `engagement_events.event_source_id` (era `comment_id`). Tasks 2-9 dependem desses nomes de coluna.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `test_decay_engine.py`, logo após os imports:

```python
def test_schema_tem_colunas_de_plataforma(tmp_path):
    db_path = tmp_path / "test.db"
    conn = de.get_connection(str(db_path))
    de.init_schema(conn)

    colunas_authors = {row[1] for row in conn.execute("PRAGMA table_info(authors)").fetchall()}
    colunas_events = {row[1] for row in conn.execute("PRAGMA table_info(engagement_events)").fetchall()}
    conn.close()

    assert "platform" in colunas_authors
    assert {"platform", "content_id", "event_source_id"} <= colunas_events
    assert "video_id" not in colunas_events
    assert "comment_id" not in colunas_events
```

- [ ] **Step 2: Rodar o teste, confirmar que falha**

Run: `pytest test_decay_engine.py::test_schema_tem_colunas_de_plataforma -v`
Expected: FAIL (`AssertionError`, colunas antigas ainda presentes / `platform` ausente).

- [ ] **Step 3: Atualizar `schema.sql`**

Substituir o conteúdo do arquivo por:

```sql
-- Schema do motor de decaimento de engajamento (energia psicologica).
-- SQLite. Indices otimizados para os padroes de acesso reais do sistema:
-- (1) leitura de eventos de UM autor ao longo do tempo (recalculo de energia),
-- (2) leitura de TODOS os eventos de UM dia (job diario em lote), e
-- (3) leitura de eventos de UMA plataforma (cutoff incremental por rede).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS authors (
    author_channel_id   TEXT PRIMARY KEY,  -- namespaced: "{platform}:{author_id}"
    platform             TEXT NOT NULL,
    author_display_name TEXT NOT NULL DEFAULT '',
    first_seen_at        TEXT NOT NULL,  -- ISO 8601 UTC
    last_seen_at          TEXT NOT NULL   -- ISO 8601 UTC
);

CREATE TABLE IF NOT EXISTS engagement_events (
    event_id           TEXT PRIMARY KEY,
    event_source_id     TEXT NOT NULL,  -- ID original na plataforma de origem (comment_id, hash de mensagem, etc.)
    platform             TEXT NOT NULL,
    author_channel_id   TEXT NOT NULL REFERENCES authors(author_channel_id),
    content_id            TEXT NOT NULL,  -- video_id, nome do grupo/chat, etc.
    published_at         TEXT NOT NULL,  -- ISO 8601 UTC
    quality_score         REAL NOT NULL CHECK (quality_score >= 0.0 AND quality_score <= 1.0),
    categorias            TEXT NOT NULL DEFAULT ''
);

-- author_channel_id isolado: usado para reconstruir a trajetoria de um autor.
CREATE INDEX IF NOT EXISTS idx_events_author_channel_id
    ON engagement_events(author_channel_id);

-- published_at isolado: usado pelo job diario para carregar so a janela do dia.
CREATE INDEX IF NOT EXISTS idx_events_published_at
    ON engagement_events(published_at);

-- composto: consultas que cruzam autor + janela temporal (ex.: churn report).
CREATE INDEX IF NOT EXISTS idx_events_author_published
    ON engagement_events(author_channel_id, published_at);

CREATE INDEX IF NOT EXISTS idx_events_content_id
    ON engagement_events(content_id);

-- platform isolado: usado pelo cutoff incremental por-plataforma.
CREATE INDEX IF NOT EXISTS idx_events_platform
    ON engagement_events(platform);

CREATE INDEX IF NOT EXISTS idx_events_platform_published
    ON engagement_events(platform, published_at);

-- Estado corrente (N0) de cada autor: um snapshot, nao um historico.
-- O historico completo, quando necessario, e reconstruido reprocessando
-- engagement_events; author_engagement_state existe para permitir o
-- calculo diario incremental sem reler o dataset inteiro.
CREATE TABLE IF NOT EXISTS author_engagement_state (
    author_channel_id TEXT PRIMARY KEY REFERENCES authors(author_channel_id),
    energy             REAL NOT NULL CHECK (energy >= 0.0 AND energy <= 100.0),
    level               INTEGER NOT NULL CHECK (level BETWEEN 1 AND 5),
    last_update_at     TEXT NOT NULL,  -- timestamp de referencia de 'energy' (N0)
    last_event_at       TEXT,           -- timestamp do ultimo evento real de engajamento
    updated_at           TEXT NOT NULL   -- quando esta linha foi recalculada
);

CREATE INDEX IF NOT EXISTS idx_state_level
    ON author_engagement_state(level);
```

- [ ] **Step 4: Rodar o teste, confirmar que passa**

Run: `pytest test_decay_engine.py::test_schema_tem_colunas_de_plataforma -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add schema.sql test_decay_engine.py
git commit -m "feat: add platform column and generic names to engagement schema"
```

---

### Task 2: `decay_engine.insert_events()` — suporte a `platform`/`content_id`/`event_source_id`

**Files:**
- Modify: `decay_engine.py:315-343` (`insert_events`)
- Modify: `test_decay_engine.py` (atualiza `test_persistencia_sqlite_round_trip`)

**Interfaces:**
- Consumes: schema de `Task 1`.
- Produces: `insert_events(conn, events)` aceita `events` com colunas
  `event_id, event_source_id, platform, author_channel_id, author_display_name,
  content_id, published_at, quality_score, categorias`. `Task 5`
  (`to_decay_engine_events`) produz exatamente essas colunas.

- [ ] **Step 1: Atualizar o teste existente que quebra com a mudança**

Em `test_decay_engine.py`, localizar `test_persistencia_sqlite_round_trip` e
trocar o bloco de montagem de `eventos`:

```python
def test_persistencia_sqlite_round_trip(tmp_path):
    db_path = tmp_path / "test.db"
    conn = de.get_connection(str(db_path))
    de.init_schema(conn)

    eventos = _eventos_dois_autores().copy()
    eventos["author_display_name"] = eventos["author_channel_id"]
    eventos["event_id"] = [f"e{i}" for i in range(len(eventos))]
    eventos["event_source_id"] = eventos["event_id"]
    eventos["platform"] = "youtube"
    eventos["content_id"] = "v1"
    eventos["categorias"] = ""
    de.insert_events(conn, eventos)

    state, _ = de.backfill_history(_eventos_dois_autores(), base_weight=20.0)
    de.save_state(conn, state)

    recarregado = de.load_state(conn)
    conn.close()

    assert recarregado.loc["A", "energy"] == pytest.approx(state.loc["A", "energy"])
    assert recarregado.loc["A", "level"] == state.loc["A", "level"]
```

Adicionar um teste novo logo abaixo, verificando que a coluna `platform` é
persistida:

```python
def test_insert_events_persiste_platform(tmp_path):
    db_path = tmp_path / "test.db"
    conn = de.get_connection(str(db_path))
    de.init_schema(conn)

    eventos = _eventos_dois_autores().copy()
    eventos["author_display_name"] = eventos["author_channel_id"]
    eventos["event_id"] = [f"e{i}" for i in range(len(eventos))]
    eventos["event_source_id"] = eventos["event_id"]
    eventos["platform"] = "whatsapp"
    eventos["content_id"] = "Grupo Teste"
    eventos["categorias"] = ""
    de.insert_events(conn, eventos)

    linhas = conn.execute("SELECT DISTINCT platform FROM engagement_events").fetchall()
    conn.close()

    assert linhas == [("whatsapp",)]
```

- [ ] **Step 2: Rodar os testes, confirmar que falham**

Run: `pytest test_decay_engine.py::test_persistencia_sqlite_round_trip test_decay_engine.py::test_insert_events_persiste_platform -v`
Expected: FAIL (`insert_events` ainda espera `comment_id`/`video_id`, sem `platform`).

- [ ] **Step 3: Atualizar `insert_events`**

Substituir a função em `decay_engine.py`:

```python
def insert_events(conn: sqlite3.Connection, events: pd.DataFrame) -> None:
    """Insere (ou atualiza) autores e eventos de engajamento a partir de um DataFrame.

    Espera as colunas: event_id, event_source_id, platform, author_channel_id,
    author_display_name, content_id, published_at, quality_score, categorias.
    """
    autores = (
        events[["author_channel_id", "author_display_name", "platform"]]
        .drop_duplicates("author_channel_id")
        .copy()
    )
    now_iso = pd.Timestamp.now(tz="UTC").isoformat()
    autores["first_seen_at"] = now_iso
    autores["last_seen_at"] = now_iso
    conn.executemany(
        """
        INSERT INTO authors (author_channel_id, author_display_name, platform, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(author_channel_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
        """,
        autores[["author_channel_id", "author_display_name", "platform", "first_seen_at", "last_seen_at"]]
        .values.tolist(),
    )

    colunas = ["event_id", "event_source_id", "platform", "author_channel_id", "content_id",
               "published_at", "quality_score", "categorias"]
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO engagement_events ({", ".join(colunas)})
        VALUES ({", ".join("?" for _ in colunas)})
        """,
        events[colunas].values.tolist(),
    )
    conn.commit()
```

- [ ] **Step 4: Rodar os testes, confirmar que passam**

Run: `pytest test_decay_engine.py -v`
Expected: todos PASS (inclui os testes já existentes, que não usam `insert_events` e continuam intactos).

- [ ] **Step 5: Commit**

```bash
git add decay_engine.py test_decay_engine.py
git commit -m "feat: add platform/content_id/event_source_id to insert_events"
```

---

### Task 3: `build_engagement_state.get_events_cutoff()` — cutoff por plataforma

**Files:**
- Modify: `build_engagement_state.py:106-111` (`get_events_cutoff`)
- Test: `test_build_engagement_state.py` (novo arquivo)

**Interfaces:**
- Consumes: schema de `Task 1`/`Task 2`.
- Produces: `get_events_cutoff(conn, platform: str) -> pd.Timestamp | None`. `Task 8` usa essa assinatura.

- [ ] **Step 1: Escrever o teste que falha**

Criar `test_build_engagement_state.py`:

```python
"""Testes de build_engagement_state.py. Rodar com: pytest test_build_engagement_state.py -v"""

import pandas as pd

import decay_engine as de
from build_engagement_state import get_events_cutoff


def _eventos_duas_plataformas():
    return pd.DataFrame(
        [
            {"event_id": "e1", "event_source_id": "e1", "platform": "youtube",
             "author_channel_id": "youtube:a", "author_display_name": "a",
             "content_id": "v1", "published_at": "2026-08-01T10:00:00+00:00",
             "quality_score": 0.5, "categorias": ""},
            {"event_id": "e2", "event_source_id": "e2", "platform": "whatsapp",
             "author_channel_id": "whatsapp:a", "author_display_name": "a",
             "content_id": "g1", "published_at": "2026-08-05T10:00:00+00:00",
             "quality_score": 0.5, "categorias": ""},
        ]
    )


class TestGetEventsCutoff:
    def test_filtra_por_plataforma(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)
        de.insert_events(conn, _eventos_duas_plataformas())

        cutoff_youtube = get_events_cutoff(conn, "youtube")
        cutoff_whatsapp = get_events_cutoff(conn, "whatsapp")
        conn.close()

        assert cutoff_youtube == pd.Timestamp("2026-08-01T10:00:00+00:00")
        assert cutoff_whatsapp == pd.Timestamp("2026-08-05T10:00:00+00:00")

    def test_sem_eventos_retorna_none(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)
        resultado = get_events_cutoff(conn, "youtube")
        conn.close()
        assert resultado is None
```

- [ ] **Step 2: Rodar o teste, confirmar que falha**

Run: `pytest test_build_engagement_state.py -v`
Expected: FAIL (`get_events_cutoff()` ainda não aceita `platform`, e a query é global).

- [ ] **Step 3: Atualizar `get_events_cutoff`**

Em `build_engagement_state.py`, substituir:

```python
def get_events_cutoff(conn, platform: str) -> pd.Timestamp | None:
    """Timestamp do evento mais recente ja gravado para essa plataforma, ou None se vazio."""
    row = conn.execute(
        "SELECT MAX(published_at) FROM engagement_events WHERE platform = ?", (platform,)
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return pd.Timestamp(row[0])
```

- [ ] **Step 4: Rodar o teste, confirmar que passa**

Run: `pytest test_build_engagement_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add build_engagement_state.py test_build_engagement_state.py
git commit -m "feat: scope incremental cutoff to a single platform"
```

---

### Task 4: `youtube_to_universal_events()` — adaptador YouTube pro schema universal

**Files:**
- Modify: `build_engagement_state.py` (extrai de `build_events_frame`, que é removida)
- Test: `test_build_engagement_state.py`

**Interfaces:**
- Consumes: DataFrame já pontuado por `load_and_score_comments()` (colunas
  `comment_id, author, video_id, published_at, quality_score`, `categorias` opcional).
- Produces: `youtube_to_universal_events(df) -> pd.DataFrame` com colunas
  `event_id, platform, author_id, author_display_name, content_id,
  published_at, quality_score, categorias`. `Task 5` consome essa saída.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar em `test_build_engagement_state.py`:

```python
from build_engagement_state import youtube_to_universal_events


class TestYoutubeToUniversalEvents:
    def test_mapeia_colunas_e_fixa_platform(self):
        df = pd.DataFrame(
            [
                {
                    "comment_id": "c1",
                    "author": "@fulano",
                    "video_id": "v1",
                    "published_at": "2026-08-01T10:00:00+00:00",
                    "quality_score": 0.7,
                    "categorias": ["elogio_generico"],
                }
            ]
        )
        resultado = youtube_to_universal_events(df)
        assert resultado.iloc[0]["platform"] == "youtube"
        assert resultado.iloc[0]["author_id"] == "@fulano"
        assert resultado.iloc[0]["content_id"] == "v1"
        assert resultado.iloc[0]["categorias"] == "elogio_generico"
        assert resultado.iloc[0]["event_id"] == "c1"

    def test_sem_categorias_vira_string_vazia(self):
        df = pd.DataFrame(
            [{"comment_id": "c1", "author": "@a", "video_id": "v1",
              "published_at": "2026-08-01T10:00:00+00:00", "quality_score": 0.5}]
        )
        resultado = youtube_to_universal_events(df)
        assert resultado.iloc[0]["categorias"] == ""

    def test_dedup_por_event_id(self):
        df = pd.DataFrame(
            [
                {"comment_id": "dup", "author": "@a", "video_id": "v1",
                 "published_at": "2026-08-01T10:00:00+00:00", "quality_score": 0.5, "categorias": []},
                {"comment_id": "dup", "author": "@a", "video_id": "v1",
                 "published_at": "2026-08-01T10:00:00+00:00", "quality_score": 0.5, "categorias": []},
            ]
        )
        resultado = youtube_to_universal_events(df)
        assert len(resultado) == 1
```

- [ ] **Step 2: Rodar os testes, confirmar que falham**

Run: `pytest test_build_engagement_state.py::TestYoutubeToUniversalEvents -v`
Expected: FAIL (`ImportError: cannot import name 'youtube_to_universal_events'`).

- [ ] **Step 3: Substituir `build_events_frame` por `youtube_to_universal_events`**

Em `build_engagement_state.py`, remover a função `build_events_frame` e adicionar no lugar:

```python
def youtube_to_universal_events(df: pd.DataFrame) -> pd.DataFrame:
    """Converte comentarios do YouTube (ja com quality_score) para o schema
    universal de eventos (mesmo contrato que qualquer outro adaptador de
    plataforma deve produzir)."""
    if "categorias" in df.columns:
        categorias = df["categorias"].apply(lambda c: ",".join(c) if isinstance(c, list) else (c or ""))
    else:
        categorias = ""

    events = pd.DataFrame(
        {
            "event_id": df["comment_id"],
            "platform": "youtube",
            "author_id": df["author"],
            "author_display_name": df["author"],
            "content_id": df["video_id"],
            "published_at": df["published_at"],
            "quality_score": df["quality_score"],
            "categorias": categorias,
        }
    )
    # comentarios duplicados/reextraidos (mesmo event_id) so entram uma vez.
    events = events.drop_duplicates("event_id")
    return events
```

- [ ] **Step 4: Rodar os testes, confirmar que passam**

Run: `pytest test_build_engagement_state.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add build_engagement_state.py test_build_engagement_state.py
git commit -m "refactor: extract youtube_to_universal_events from build_events_frame"
```

---

### Task 5: `to_decay_engine_events()` — tradutor universal → interno (namespacing de autor)

**Files:**
- Modify: `build_engagement_state.py`
- Test: `test_build_engagement_state.py`

**Interfaces:**
- Consumes: schema universal (`Task 4`, e `whatsapp_extractor.py` da `Task 7`).
- Produces: `to_decay_engine_events(universal_events) -> pd.DataFrame` com
  colunas extras `author_channel_id` (namespaced `"{platform}:{author_id}"`)
  e `event_source_id`. `Task 8` (`process_platform`) consome essa saída antes
  de chamar `decay_engine.insert_events`/`backfill_history`.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar em `test_build_engagement_state.py`:

```python
from build_engagement_state import to_decay_engine_events


def _evento_universal(**overrides):
    base = {
        "event_id": "e1", "platform": "youtube", "author_id": "joao",
        "author_display_name": "joao", "content_id": "v1",
        "published_at": "2026-08-01T10:00:00+00:00", "quality_score": 0.5, "categorias": "",
    }
    base.update(overrides)
    return base


class TestToDecayEngineEvents:
    def test_namespacing_evita_colisao_entre_plataformas(self):
        universal = pd.DataFrame(
            [
                _evento_universal(event_id="e1", platform="youtube"),
                _evento_universal(event_id="e2", platform="whatsapp", content_id="Grupo X"),
            ]
        )
        resultado = to_decay_engine_events(universal)
        assert set(resultado["author_channel_id"]) == {"youtube:joao", "whatsapp:joao"}

    def test_event_source_id_copia_event_id(self):
        universal = pd.DataFrame([_evento_universal(event_id="e1")])
        resultado = to_decay_engine_events(universal)
        assert resultado.iloc[0]["event_source_id"] == "e1"
```

- [ ] **Step 2: Rodar os testes, confirmar que falham**

Run: `pytest test_build_engagement_state.py::TestToDecayEngineEvents -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implementar `to_decay_engine_events`**

Adicionar em `build_engagement_state.py`, logo após `youtube_to_universal_events`:

```python
def to_decay_engine_events(universal_events: pd.DataFrame) -> pd.DataFrame:
    """Traduz o schema universal (author_id + platform) para o formato interno
    do decay_engine. Namespacia author_channel_id = "{platform}:{author_id}"
    para evitar colisao entre plataformas sem precisar de chave composta em
    pandas -- decay_engine.py continua so enxergando um author_channel_id
    string, sem saber que carrega a plataforma embutida."""
    events = universal_events.copy()
    events["author_channel_id"] = events["platform"] + ":" + events["author_id"].astype(str)
    events["event_source_id"] = events["event_id"]
    return events
```

- [ ] **Step 4: Rodar os testes, confirmar que passam**

Run: `pytest test_build_engagement_state.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add build_engagement_state.py test_build_engagement_state.py
git commit -m "feat: add to_decay_engine_events translator with platform namespacing"
```

---

### Task 6: `whatsapp_extractor.py` — parsing do export `.txt`

**Files:**
- Create: `whatsapp_extractor.py`
- Test: `test_whatsapp_extractor.py`

**Interfaces:**
- Consumes: nada (lê arquivo local passado por caminho).
- Produces: `parse_whatsapp_export(texto: str) -> list[dict]` (chaves `author`,
  `timestamp_raw`, `text`), `is_system_message(texto: str) -> bool`,
  `make_event_id(author, published_at, text) -> str`. `Task 7` consome essas
  três funções.

- [ ] **Step 1: Escrever os testes que falham**

Criar `test_whatsapp_extractor.py`:

```python
"""Testes de whatsapp_extractor.py. Rodar com: pytest test_whatsapp_extractor.py -v"""

from whatsapp_extractor import parse_whatsapp_export, is_system_message, make_event_id


class TestParseWhatsappExport:
    def test_mensagem_simples(self):
        texto = "12/08/2026 22:10 - Joao Silva: oi pessoal, tudo bem?"
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 1
        assert resultado[0]["author"] == "Joao Silva"
        assert resultado[0]["text"] == "oi pessoal, tudo bem?"
        assert resultado[0]["timestamp_raw"] == "12/08/2026 22:10"

    def test_mensagem_multilinha_concatena_na_anterior(self):
        texto = "12/08/2026 22:10 - Joao Silva: primeira linha\nsegunda linha sem prefixo"
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 1
        assert resultado[0]["text"] == "primeira linha\nsegunda linha sem prefixo"

    def test_duas_mensagens_distintas(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: primeira\n"
            "12/08/2026 22:11 - Maria Souza: segunda"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[1]["author"] == "Maria Souza"

    def test_mensagem_de_sistema_sem_dois_pontos_e_ignorada(self):
        texto = (
            "12/08/2026 22:00 - As mensagens e as ligacoes agora sao protegidas "
            "com criptografia de ponta a ponta.\n"
            "12/08/2026 22:10 - Joao Silva: oi"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 1
        assert resultado[0]["author"] == "Joao Silva"

    def test_linha_vazia_ignorada(self):
        texto = "12/08/2026 22:10 - Joao Silva: oi\n\n"
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 1

    def test_arquivo_vazio(self):
        assert parse_whatsapp_export("") == []


class TestIsSystemMessage:
    def test_midia_oculta(self):
        assert is_system_message("<Midia oculta>") is True

    def test_figurinha(self):
        assert is_system_message("figurinha omitida") is True

    def test_mensagem_normal_nao_e_sistema(self):
        assert is_system_message("gostei muito da explicacao, obrigado!") is False


class TestMakeEventId:
    def test_determinismo(self):
        a = make_event_id("Joao", "2026-08-12T22:10:00+00:00", "oi")
        b = make_event_id("Joao", "2026-08-12T22:10:00+00:00", "oi")
        assert a == b

    def test_inputs_diferentes_geram_ids_diferentes(self):
        a = make_event_id("Joao", "2026-08-12T22:10:00+00:00", "oi")
        b = make_event_id("Maria", "2026-08-12T22:10:00+00:00", "oi")
        assert a != b
```

- [ ] **Step 2: Rodar os testes, confirmar que falham**

Run: `pytest test_whatsapp_extractor.py -v`
Expected: `ModuleNotFoundError: No module named 'whatsapp_extractor'`.

- [ ] **Step 3: Criar `whatsapp_extractor.py` com parsing**

```python
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
```

- [ ] **Step 4: Rodar os testes, confirmar que passam**

Run: `pytest test_whatsapp_extractor.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add whatsapp_extractor.py test_whatsapp_extractor.py
git commit -m "feat: add WhatsApp export parsing (whatsapp_extractor.py)"
```

---

### Task 7: `whatsapp_extractor.py` — scoring, schema universal e CLI

**Files:**
- Modify: `whatsapp_extractor.py`
- Test: `test_whatsapp_extractor.py`

**Interfaces:**
- Consumes: `parse_whatsapp_export`, `is_system_message`, `make_event_id` (`Task 6`); `HeuristicScorer.score_batch` (`scoring_engine.py`, já existente).
- Produces: `build_whatsapp_events(mensagens, grupo) -> pd.DataFrame` no schema
  universal (mesmas colunas de `youtube_to_universal_events`), `save_events(events, output_path)`,
  `main()` (CLI). `Task 8` lê o arquivo gerado por `main()`
  (`data/whatsapp_eventos.json`).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar em `test_whatsapp_extractor.py`:

```python
import pandas as pd

from whatsapp_extractor import build_whatsapp_events


class TestBuildWhatsappEvents:
    def test_filtra_mensagem_de_sistema(self):
        mensagens = [
            {"author": "Joao", "timestamp_raw": "12/08/2026 22:10", "text": "<Midia oculta>"},
            {"author": "Joao", "timestamp_raw": "12/08/2026 22:11", "text": "boa explicacao, ajudou bastante!"},
        ]
        resultado = build_whatsapp_events(mensagens, grupo="Grupo Teste")
        assert len(resultado) == 1
        assert resultado.iloc[0]["platform"] == "whatsapp"
        assert resultado.iloc[0]["content_id"] == "Grupo Teste"
        assert resultado.iloc[0]["author_id"] == "Joao"

    def test_lista_vazia_apos_filtro_retorna_dataframe_com_schema_universal(self):
        mensagens = [{"author": "Joao", "timestamp_raw": "12/08/2026 22:10", "text": "<Midia oculta>"}]
        resultado = build_whatsapp_events(mensagens, grupo="Grupo Teste")
        assert resultado.empty
        assert list(resultado.columns) == [
            "event_id", "platform", "author_id", "author_display_name",
            "content_id", "published_at", "quality_score", "categorias",
        ]

    def test_quality_score_no_intervalo(self):
        mensagens = [
            {"author": "Joao", "timestamp_raw": "12/08/2026 22:10",
             "text": "Sera que da pra explicar melhor esse ponto? Fiquei com duvida."},
        ]
        resultado = build_whatsapp_events(mensagens, grupo="Grupo Teste")
        assert 0.0 <= resultado.iloc[0]["quality_score"] <= 1.0

    def test_published_at_e_iso_utc(self):
        mensagens = [{"author": "Joao", "timestamp_raw": "12/08/2026 22:10", "text": "boa pergunta, obrigado!"}]
        resultado = build_whatsapp_events(mensagens, grupo="Grupo Teste")
        assert resultado.iloc[0]["published_at"].startswith("2026-08-12T22:10:00")
```

- [ ] **Step 2: Rodar os testes, confirmar que falham**

Run: `pytest test_whatsapp_extractor.py::TestBuildWhatsappEvents -v`
Expected: FAIL (`ImportError: cannot import name 'build_whatsapp_events'`).

- [ ] **Step 3: Completar `whatsapp_extractor.py`**

Substituir o bloco final (`if __name__ == "__main__": pass`) por:

```python
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
```

- [ ] **Step 4: Rodar os testes, confirmar que passam**

Run: `pytest test_whatsapp_extractor.py -v`
Expected: todos PASS.

- [ ] **Step 5: Sanity check manual**

```bash
mkdir -p /tmp/wa_teste
cat > /tmp/wa_teste/grupo.txt <<'EOF'
12/08/2026 22:00 - As mensagens e as ligacoes agora sao protegidas com criptografia de ponta a ponta.
12/08/2026 22:10 - Joao Silva: Pessoal, alguem sabe se da pra usar isso em producao?
12/08/2026 22:11 - Maria Souza: <Midia oculta>
12/08/2026 22:12 - Joao Silva: Testei aqui e funcionou bem, so precisei ajustar um parametro.
EOF
python whatsapp_extractor.py --input /tmp/wa_teste/grupo.txt --grupo "Grupo Teste" --output /tmp/wa_teste/eventos.json
python -c "import json; print(json.load(open('/tmp/wa_teste/eventos.json')))"
```
Expected: 2 eventos (Maria's `<Midia oculta>` filtrada, aviso de sistema
descartado), `platform` = `"whatsapp"`, `content_id` = `"Grupo Teste"`.

- [ ] **Step 6: Commit**

```bash
git add whatsapp_extractor.py test_whatsapp_extractor.py
git commit -m "feat: score WhatsApp messages and write universal event schema"
```

---

### Task 8: `build_engagement_state.py` — orquestração por plataforma

**Files:**
- Modify: `build_engagement_state.py` (substitui `main()` e adiciona `process_platform`, `load_whatsapp_events`)
- Test: `test_build_engagement_state.py`

**Interfaces:**
- Consumes: `youtube_to_universal_events` (`Task 4`), `to_decay_engine_events` (`Task 5`),
  `get_events_cutoff` (`Task 3`), `decay_engine.insert_events`/`backfill_history`/`load_state`/`save_state`.
- Produces: `engagement.db` populado com eventos e estado de ambas as
  plataformas presentes em `./data/`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `test_build_engagement_state.py`:

```python
from build_engagement_state import process_platform


class TestProcessPlatform:
    def test_grava_eventos_e_estado_para_uma_plataforma(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)

        universal = pd.DataFrame(
            [
                _evento_universal_wa(author_id="joao", published_at="2026-08-01T10:00:00+00:00"),
                _evento_universal_wa(author_id="joao", published_at="2026-08-02T10:00:00+00:00", event_id="e2"),
            ]
        )
        process_platform(conn, "whatsapp", universal, base_weight=20.0, banco_existente=False)

        estado = de.load_state(conn)
        eventos_gravados = conn.execute("SELECT COUNT(*) FROM engagement_events").fetchone()[0]
        conn.close()

        assert "whatsapp:joao" in estado.index
        assert eventos_gravados == 2

    def test_plataforma_sem_eventos_nao_quebra(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)

        vazio = pd.DataFrame(columns=[
            "event_id", "platform", "author_id", "author_display_name",
            "content_id", "published_at", "quality_score", "categorias",
        ])
        process_platform(conn, "whatsapp", vazio, base_weight=20.0, banco_existente=False)
        conn.close()  # nao deve levantar excecao

    def test_segunda_plataforma_nao_apaga_estado_da_primeira(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = de.get_connection(str(db_path))
        de.init_schema(conn)

        yt = pd.DataFrame([_evento_universal(event_id="y1", platform="youtube", author_id="ana")])
        wa = pd.DataFrame([_evento_universal_wa(author_id="joao")])

        process_platform(conn, "youtube", yt, base_weight=20.0, banco_existente=False)
        process_platform(conn, "whatsapp", wa, base_weight=20.0, banco_existente=True)

        estado = de.load_state(conn)
        conn.close()

        assert "youtube:ana" in estado.index
        assert "whatsapp:joao" in estado.index


def _evento_universal_wa(author_id="joao", published_at="2026-08-01T10:00:00+00:00", event_id="e1"):
    return _evento_universal(
        event_id=event_id, platform="whatsapp", author_id=author_id,
        content_id="Grupo X", published_at=published_at,
    )
```

- [ ] **Step 2: Rodar o teste, confirmar que falha**

Run: `pytest test_build_engagement_state.py::TestProcessPlatform -v`
Expected: FAIL (`ImportError: cannot import name 'process_platform'`).

- [ ] **Step 3: Reescrever a orquestração em `build_engagement_state.py`**

Remover a função `main()` atual e adicionar, antes dela:

```python
WHATSAPP_EVENTS_PATH = "./data/whatsapp_eventos.json"

PLATFORM_BASE_WEIGHTS_DEFAULT = {"youtube": decay_engine.DEFAULT_BASE_WEIGHT, "whatsapp": decay_engine.DEFAULT_BASE_WEIGHT}


def load_whatsapp_events(path: str) -> pd.DataFrame:
    events_file = Path(path)
    columns = ["event_id", "platform", "author_id", "author_display_name",
               "content_id", "published_at", "quality_score", "categorias"]
    if not events_file.exists():
        return pd.DataFrame(columns=columns)
    with open(events_file, encoding="utf-8") as f:
        raw = json.load(f)
    if not raw:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(raw)


def process_platform(conn, platform: str, universal_events: pd.DataFrame,
                      base_weight: float, banco_existente: bool) -> None:
    """Processa os eventos de UMA plataforma: cutoff incremental proprio,
    insercao no SQLite e replay do motor de decaimento com o estado
    (filtrado por plataforma) que ja existia."""
    if universal_events.empty:
        print(f"[{platform}] nenhum evento encontrado, pulando.")
        return

    events = to_decay_engine_events(universal_events)

    cutoff = get_events_cutoff(conn, platform) if banco_existente else None
    if cutoff is not None:
        antes = len(events)
        events = events[pd.to_datetime(events["published_at"], utc=True) > cutoff]
        print(f"[{platform}] modo incremental: banco ja tem eventos ate {cutoff}. "
              f"{len(events)}/{antes} sao novos.")
        if events.empty:
            print(f"[{platform}] nada novo para processar.")
            return
    else:
        print(f"[{platform}] modo completo: processando {len(events)} eventos do zero.")

    estado_completo = decay_engine.load_state(conn) if banco_existente else pd.DataFrame()
    if not estado_completo.empty:
        estado_plataforma = estado_completo[estado_completo.index.str.startswith(f"{platform}:")]
    else:
        estado_plataforma = estado_completo

    print(f"[{platform}] distribuicao de nivel instantaneo por evento:")
    for nivel, contagem in instant_level_distribution(events).items():
        print(f"  L{nivel}: {contagem} eventos")

    insert_events_in_chunks(conn, events)

    state, _ = decay_engine.backfill_history(
        events, base_weight=base_weight, keep_history=False,
        initial_state=estado_plataforma if not estado_plataforma.empty else None,
    )
    decay_engine.save_state(conn, state)
    print(f"[{platform}] {len(state)} autores atualizados.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-weight-youtube", type=float, default=PLATFORM_BASE_WEIGHTS_DEFAULT["youtube"])
    parser.add_argument("--base-weight-whatsapp", type=float, default=PLATFORM_BASE_WEIGHTS_DEFAULT["whatsapp"])
    parser.add_argument("--db", type=str, default=decay_engine.DB_PATH)
    parser.add_argument("--raw", type=str, default=RAW_COMMENTS_PATH)
    parser.add_argument("--classified", type=str, default=CLASSIFIED_COMMENTS_PATH)
    parser.add_argument("--whatsapp-events", type=str, default=WHATSAPP_EVENTS_PATH)
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Forca reconstrucao completa do banco, mesmo se ele ja existir com dados.",
    )
    args = parser.parse_args()

    t_inicio = time.time()

    db_path = Path(args.db)
    banco_existente = db_path.exists()
    if banco_existente and args.rebuild:
        db_path.unlink()
        print(f"\n--rebuild: banco anterior {db_path} removido para reconstrucao completa.")
        banco_existente = False

    conn = decay_engine.get_connection(str(db_path))
    decay_engine.init_schema(conn)

    df_youtube = load_and_score_comments(args.raw, args.classified)
    eventos_youtube = youtube_to_universal_events(df_youtube)
    del df_youtube

    eventos_whatsapp = load_whatsapp_events(args.whatsapp_events)

    base_weights = {"youtube": args.base_weight_youtube, "whatsapp": args.base_weight_whatsapp}
    for platform, universal_events in (("youtube", eventos_youtube), ("whatsapp", eventos_whatsapp)):
        print(f"\n=== Processando plataforma: {platform} ===")
        process_platform(conn, platform, universal_events, base_weights[platform], banco_existente)

    conn.close()
    print(f"\nTotal: {time.time() - t_inicio:.1f}s. Banco salvo em {db_path}.")
    print("Rode 'streamlit run app.py' para explorar visualmente.")


if __name__ == "__main__":
    main()
```

Adicionar também, no topo do arquivo, o helper de fixture usado pelo teste
(`_evento_universal`) em `test_build_engagement_state.py` (caso ainda não
exista de uma task anterior — reutilizar se já estiver lá).

- [ ] **Step 4: Rodar os testes, confirmar que passam**

Run: `pytest test_build_engagement_state.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add build_engagement_state.py test_build_engagement_state.py
git commit -m "refactor: orchestrate build_engagement_state.py per platform"
```

---

### Task 9: Testes de integração cross-platform em `test_decay_engine.py`

**Files:**
- Modify: `test_decay_engine.py`

**Interfaces:**
- Consumes: `decay_engine.backfill_history`, `decay_engine.churn_risk_report` (já existentes, sem mudança de assinatura).
- Produces: cobertura de regressão garantindo que autores namespaced de
  plataformas diferentes não colidem em nenhum ponto do motor.

- [ ] **Step 1: Escrever os testes**

Adicionar ao final de `test_decay_engine.py`:

```python
# --------------------------------------------------------------------------
# cross-platform (namespacing de author_channel_id)
# --------------------------------------------------------------------------

def test_duas_plataformas_com_mesmo_author_id_nao_colidem():
    eventos_youtube = pd.DataFrame(
        [{"author_channel_id": "youtube:joao", "published_at": "2026-01-01T10:00:00Z", "quality_score": 1.0}]
    )
    eventos_whatsapp = pd.DataFrame(
        [{"author_channel_id": "whatsapp:joao", "published_at": "2026-01-01T10:00:00Z", "quality_score": 1.0}]
    )

    estado_youtube, _ = de.backfill_history(eventos_youtube, base_weight=20.0)
    estado_whatsapp, _ = de.backfill_history(eventos_whatsapp, base_weight=5.0)

    estado_final = pd.concat([estado_youtube, estado_whatsapp])

    assert set(estado_final.index) == {"youtube:joao", "whatsapp:joao"}
    assert estado_final.loc["youtube:joao", "energy"] != estado_final.loc["whatsapp:joao", "energy"]


def test_churn_risk_report_funciona_com_autores_de_plataformas_diferentes():
    agora = pd.Timestamp.now(tz="UTC")
    estado = pd.DataFrame(
        {
            "energy": [65.0, 65.0],
            "level": [4, 4],
            "last_update_at": [agora, agora],
            "last_event_at": [agora, agora],
            "updated_at": [agora, agora],
        },
        index=pd.Index(["youtube:joao", "whatsapp:joao"], name="author_channel_id"),
    )
    risco = de.churn_risk_report(estado, buffer=50.0)
    assert set(risco.index) == {"youtube:joao", "whatsapp:joao"}
```

- [ ] **Step 2: Rodar os testes, confirmar que passam**

Run: `pytest test_decay_engine.py -v`
Expected: todos PASS — confirma que `decay_engine.py` não precisou de nenhuma
mudança para suportar múltiplas plataformas (só recebe strings namespaced).

- [ ] **Step 3: Rodar a suite inteira do projeto pra checar regressão**

Run: `pytest test_scoring_engine.py test_decay_engine.py test_build_engagement_state.py test_whatsapp_extractor.py -v`
Expected: todos PASS.

- [ ] **Step 4: Commit**

```bash
git add test_decay_engine.py
git commit -m "test: add cross-platform namespacing regression coverage"
```

---

### Task 10: Documentação (`AGENTS.md`) e validação end-to-end

**Files:**
- Modify: `AGENTS.md:18-63` (seção "Motor de Decaimento de Engajamento")

**Interfaces:**
- Consumes: todas as tasks anteriores.
- Produces: documentação atualizada; confirmação de que o pipeline completo roda sem erro.

- [ ] **Step 1: Atualizar a seção do motor de decaimento em `AGENTS.md`**

Substituir o parágrafo de abertura da seção (linha 20-23) por:

```markdown
Subsistema separado do pipeline de extração/classificação acima: identifica
autores em risco de evasão via decaimento físico de uma "energia" por autor,
com 5 níveis psicológicos (L1-L5) e transições com histerese (banda de 3
pontos na queda de nível, pra não oscilar por ruído). Ingestão é
plataforma-agnóstica: qualquer rede alimenta o motor através de um schema
universal de evento (`event_id, platform, author_id, author_display_name,
content_id, published_at, quality_score, categorias`), traduzido para o
formato interno via `author_channel_id = "{platform}:{author_id}"` (evita
colisão entre plataformas sem exigir chave composta). Hoje: YouTube
(comentários, via `main.py`) e WhatsApp (export manual de chat `.txt`, via
`whatsapp_extractor.py`).
```

Adicionar, logo após o bullet "**Arquivos**" (linha 25-31), um novo bullet:

```markdown
- **Adaptador WhatsApp**: `whatsapp_extractor.py` lê um export manual do
  WhatsApp (`Exportar conversa` no menu do grupo, formato Android
  `DD/MM/AAAA HH:MM - Autor: mensagem`), filtra mensagens de sistema
  (criptografia, mídia oculta, entrada/saída de membro) e pontua cada
  mensagem com `HeuristicScorer` (sem custo de LLM). Roda separado:
  `python whatsapp_extractor.py --input data/whatsapp_bruto_<grupo>.txt
  --grupo "Nome do Grupo"`, gera `data/whatsapp_eventos.json`, que
  `build_engagement_state.py` lê automaticamente se existir. Testes em
  `test_whatsapp_extractor.py`.
```

Substituir o bullet "**Como rodar**" (linha 33-37) por:

```markdown
- **Como rodar**: `python build_engagement_state.py` popula/atualiza
  `engagement.db`, processando cada plataforma presente em `./data/`
  separadamente (cutoff incremental e `base_weight` independentes por
  plataforma — `--base-weight-youtube`/`--base-weight-whatsapp`, default 20
  pros dois). `--rebuild` força reprocessar tudo. **Mudança de schema**: bancos
  `engagement.db` criados antes desta versão não têm a coluna `platform` —
  rode com `--rebuild` uma vez após atualizar. `streamlit run app.py` abre o
  dashboard (usa `engagement.db` se existir, senão gera dados sintéticos na
  hora).
```

- [ ] **Step 2: Rodar a suite completa do projeto**

Run: `pytest test_scoring_engine.py test_decay_engine.py test_build_engagement_state.py test_whatsapp_extractor.py -v`
Expected: todos PASS.

- [ ] **Step 3: Validação end-to-end manual (WhatsApp isolado)**

```bash
rm -f engagement.db
mkdir -p data
cat > data/whatsapp_bruto_teste.txt <<'EOF'
12/08/2026 09:00 - Ana: Bom dia! Alguem testou a nova versao ja?
12/08/2026 09:05 - Bruno: <Midia oculta>
12/08/2026 09:10 - Ana: Testei sim, funcionou bem, so precisei ajustar a config inicial.
EOF
python whatsapp_extractor.py --input data/whatsapp_bruto_teste.txt --grupo "Grupo Teste" --output data/whatsapp_eventos.json
python build_engagement_state.py --rebuild
python -c "
import decay_engine as de
conn = de.get_connection()
estado = de.load_state(conn)
conn.close()
print(estado.index.tolist())
"
```
Expected: sem exceção; `estado.index.tolist()` inclui `"whatsapp:Ana"` (e
não inclui `"whatsapp:Bruno"`, já que a única mensagem dele foi filtrada por
ser mídia oculta).

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs: document multi-platform ingestion in the decay engine section"
```
