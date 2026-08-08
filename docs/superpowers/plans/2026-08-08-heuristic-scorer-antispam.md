# HeuristicScorer Anti-Spam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `HeuristicScorer` (the no-LLM comment scorer in `scoring_engine.py`) resistant to well-written spam — self-promotion phrases and mass-duplicated text — and wire the real pipeline to actually use the batch-aware scoring path.

**Architecture:** Two additions confined to the `HeuristicScorer` class: a per-comment regex check for self-promo phrases inside `score()`, and a vectorized (pandas `value_counts`) duplicate-detection pass inside an overridden `score_batch()`. `build_engagement_state.py` switches from manual list comprehensions calling `.score()` to calling `.score_batch()` for both scorers, so the new duplicate check actually fires in the real pipeline.

**Tech Stack:** Python, pandas, `re`, pytest.

## Global Constraints

- No LLM calls anywhere in this change — spec requirement, `HeuristicScorer` must stay fully local/offline.
- Duplicate-counting across a batch must be vectorized (pandas `value_counts`/`map`), not a Python loop counting occurrences one string at a time — matches the vectorization standard already used in `decay_engine.py`.
- `CategoryWeightedScorer` behavior must not change.
- `decay_engine.py` and `schema.sql` are not touched by this plan.
- This project has no git repository yet (init was explicitly deferred by the user). Do **not** run `git add`/`git commit` as part of this plan — skip the commit step in each task and just leave the work staged in the working tree.
- Spec: `docs/superpowers/specs/2026-08-08-heuristic-scorer-antispam-design.md`.

---

### Task 1: Self-promo detection in `HeuristicScorer.score()`

**Files:**
- Modify: `scoring_engine.py:31-61` (class `HeuristicScorer`, method `score`)
- Test: `test_scoring_engine.py` (class `TestHeuristicScorer`)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `HeuristicScorer.SELF_PROMO_PATTERNS: list[re.Pattern]` (class attribute) — Task 2 does not depend on this, but keep the name stable since it's referenced in the spec.

- [ ] **Step 1: Write the failing tests**

Add to `test_scoring_engine.py`, inside `class TestHeuristicScorer` (after `test_pergunta_recebe_bonus`, before `test_score_sempre_em_faixa_valida`):

```python
    def test_autopromocao_score_zero(self):
        frases = [
            "Confira meu canal lá no instagram, tem conteúdo top!",
            "Segue lá, tem link na bio pra quem quiser conferir",
            "Gente, me segue lá que eu posto todo dia",
            "Clique no link da descrição pra saber mais",
        ]
        for frase in frases:
            assert self.scorer.score({"text": frase}) == 0.0

    def test_texto_legitimo_com_palavra_canal_nao_e_zerado(self):
        texto = "Gostei muito do canal de vocês, o conteúdo é sempre muito bem explicado"
        assert self.scorer.score({"text": texto}) > 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_scoring_engine.py -k "autopromocao or legitimo_com_palavra_canal" -v`
Expected: both FAIL (`test_autopromocao_score_zero` fails because current scorer returns a nonzero score for these phrases; `test_texto_legitimo...` currently passes already but re-run it together to confirm nothing is broken yet).

- [ ] **Step 3: Add `SELF_PROMO_PATTERNS` and the check in `score()`**

In `scoring_engine.py`, inside `class HeuristicScorer`, add the class attribute right after `LENGTH_CAP_CHARS = 240`:

```python
    SELF_PROMO_PATTERNS = [
        re.compile(
            r"(confir[ae]|visit[ae]|segu[ae]|inscrev[ae]|assist[ae])"
            r".{0,25}(meu|nosso).{0,10}(canal|perfil|instagram|insta)",
            re.IGNORECASE,
        ),
        re.compile(r"link\s*na\s*bio", re.IGNORECASE),
        re.compile(r"me\s+segue", re.IGNORECASE),
        re.compile(r"(clique|acesse)\s+(no|o)\s+link", re.IGNORECASE),
    ]
```

Then in `score()`, add the check right after the existing empty/laughter early-return block and before `comprimento_normalizado = ...`:

```python
        sem_emoji = EMOJI_PATTERN.sub("", text).strip()
        if len(sem_emoji) <= 2 or re.fullmatch(r"(k|h|s|rs|haha)+[\s!.]*", sem_emoji.lower()):
            return 0.0

        if any(padrao.search(text) for padrao in self.SELF_PROMO_PATTERNS):
            return 0.0

        comprimento_normalizado = min(len(sem_emoji), self.LENGTH_CAP_CHARS) / self.LENGTH_CAP_CHARS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_scoring_engine.py -v`
Expected: all tests PASS (the two new ones plus every pre-existing test in the file — this confirms the new check doesn't break the existing suite).

---

### Task 2: Vectorized duplicate detection in `HeuristicScorer.score_batch()`

**Files:**
- Modify: `scoring_engine.py:1-16` (imports) and `scoring_engine.py:31-61` (add `score_batch` override to `HeuristicScorer`)
- Test: `test_scoring_engine.py` (class `TestHeuristicScorer`)

**Interfaces:**
- Consumes: `HeuristicScorer.score(self, comment: dict) -> float` from Task 1 (unchanged signature).
- Produces: `HeuristicScorer.score_batch(self, comments: list[dict]) -> list[float]` (overrides the ABC default) — Task 3 calls this directly.

- [ ] **Step 1: Write the failing tests**

Add to `test_scoring_engine.py`, inside `class TestHeuristicScorer` (after the tests from Task 1):

```python
    def test_duplicata_longa_em_lote_score_zero(self):
        texto_longo = "Esse video mudou minha forma de pensar sobre o assunto"
        comentarios = [{"text": texto_longo}] * 3
        resultados = self.scorer.score_batch(comentarios)
        assert resultados == [0.0, 0.0, 0.0]

    def test_duplicata_curta_nao_e_zerada(self):
        comentarios = [{"text": "top demais"}] * 5
        resultados = self.scorer.score_batch(comentarios)
        individual = self.scorer.score({"text": "top demais"})
        assert individual > 0.0
        assert resultados == [individual] * 5

    def test_duplicata_abaixo_do_minimo_nao_e_zerada(self):
        texto_longo = "Esse video mudou minha forma de pensar sobre o assunto"
        comentarios = [{"text": texto_longo}] * 2
        resultados = self.scorer.score_batch(comentarios)
        individual = self.scorer.score({"text": texto_longo})
        assert resultados == [individual, individual]

    def test_score_batch_ainda_bate_com_score_individual_sem_duplicata(self):
        comentarios = [
            {"text": "Muito bom esse video, aprendi bastante!"},
            {"text": "Qual a fonte desse dado que voce citou?"},
            {"text": "kkkkk"},
        ]
        resultados = self.scorer.score_batch(comentarios)
        assert resultados == [self.scorer.score(c) for c in comentarios]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_scoring_engine.py -k duplicata -v`
Expected: `test_duplicata_longa_em_lote_score_zero` FAILS (current `score_batch` is the ABC default, which just calls `score()` per item — no duplicate is zeroed). The other three should already pass against the ABC default, but run them together to have a clean baseline before Step 3.

- [ ] **Step 3: Add `import pandas as pd` and override `score_batch`**

At the top of `scoring_engine.py`, add the import after `from abc import ABC, abstractmethod`:

```python
import re
from abc import ABC, abstractmethod

import pandas as pd
```

In `class HeuristicScorer`, add these two class constants right after `SELF_PROMO_PATTERNS` (from Task 1):

```python
    DUP_MIN_CHARS = 40
    DUP_MIN_COUNT = 3
```

Then add the `score_batch` override as a new method on `HeuristicScorer`, after `score()`:

```python
    def score_batch(self, comments: list[dict]) -> list[float]:
        textos = pd.Series([(c.get("text") or "").strip().lower() for c in comments])
        normalizados = textos.str.replace(r"[^\w\s]", "", regex=True).str.strip()

        contagem = normalizados.value_counts()
        e_repetido = (normalizados.map(contagem) >= self.DUP_MIN_COUNT).to_numpy()
        e_longo = (normalizados.str.len() > self.DUP_MIN_CHARS).to_numpy()
        e_duplicata_spam = e_repetido & e_longo

        scores = [self.score(c) for c in comments]
        return [0.0 if dup else s for dup, s in zip(e_duplicata_spam, scores)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_scoring_engine.py -v`
Expected: all tests PASS, including every test from Task 1.

---

### Task 3: Wire `build_engagement_state.py` to use `score_batch()`

**Files:**
- Modify: `build_engagement_state.py:43-78` (function `load_and_score_comments`)

**Interfaces:**
- Consumes: `CategoryWeightedScorer.score_batch(self, comments: list[dict]) -> list[float]` (ABC default, unchanged behavior) and `HeuristicScorer.score_batch(...)` from Task 2.
- Produces: nothing new consumed elsewhere — this is the pipeline's integration point, verified by a smoke run in Step 4, not by a unit test.

- [ ] **Step 1: Change the `CategoryWeightedScorer` branch**

In `build_engagement_state.py`, inside `load_and_score_comments`, replace:

```python
        scorer = CategoryWeightedScorer()
        df["quality_score"] = [
            scorer.score({"categorias": cats, "score_engajamento": score})
            for cats, score in zip(df.get("categorias", [[]] * len(df)), df.get("score_engajamento", [None] * len(df)))
        ]
```

with:

```python
        scorer = CategoryWeightedScorer()
        comentarios = [
            {"categorias": cats, "score_engajamento": score}
            for cats, score in zip(df.get("categorias", [[]] * len(df)), df.get("score_engajamento", [None] * len(df)))
        ]
        df["quality_score"] = scorer.score_batch(comentarios)
```

- [ ] **Step 2: Change the `HeuristicScorer` branch**

In the same function, replace:

```python
    scorer = HeuristicScorer()
    textos = df["text"].fillna("")
    df["quality_score"] = [scorer.score({"text": t}) for t in textos]
```

with:

```python
    scorer = HeuristicScorer()
    textos = df["text"].fillna("")
    comentarios = [{"text": t} for t in textos]
    df["quality_score"] = scorer.score_batch(comentarios)
```

- [ ] **Step 3: Run the full test suite**

Run: `pytest test_scoring_engine.py test_decay_engine.py -v`
Expected: all tests PASS (this change doesn't touch `decay_engine.py`, but re-running confirms nothing downstream broke).

- [ ] **Step 4: Smoke-test the real pipeline without touching production data**

Run against a throwaway database so the real `engagement.db` is untouched:

```bash
python build_engagement_state.py --rebuild --db /tmp/claude-1000/-home-jao-Documentos-projetos-ext/13bcc256-f0df-41f3-a5df-96225da45853/scratchpad/smoke_engagement.db
```

Expected: script runs to completion (same ~190s ballpark as previous full rebuilds), prints the instant-level distribution and the final per-author level distribution with no traceback. Since the real dataset already has `data/comentarios_classificados.json`, this exercises the `CategoryWeightedScorer` + `score_batch` path end-to-end — confirming the integration change didn't break the production path. Delete the throwaway db file afterward:

```bash
rm /tmp/claude-1000/-home-jao-Documentos-projetos-ext/13bcc256-f0df-41f3-a5df-96225da45853/scratchpad/smoke_engagement.db
```

---

## Self-Review Notes

- **Spec coverage:** self-promo detection (Task 1), batch duplicate detection (Task 2), `build_engagement_state.py` integration (Task 3), all six new test cases from the spec's "Testes" section are included across Tasks 1–2. Covered.
- **Placeholders:** none — every step has literal code, not a description.
- **Type consistency:** `score_batch(self, comments: list[dict]) -> list[float]` matches the ABC signature in `EngagementScorer.score_batch` and is used identically in Task 3's calls.
- **No git commits included**, per the Global Constraints note on this project not having a repo yet.
