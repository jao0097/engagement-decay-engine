# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Extracts YouTube comments, classifies engagement type via an LLM (Groq), stores results in
ChromaDB for semantic queries, and runs a separate "engagement decay" subsystem that models
super-fan churn risk over time. Code and comments are in Portuguese.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp env.example .env   # fill in YOUTUBE_API_KEY and GROQ_API_KEY (GROQ_API_KEY_2 optional fallback)
```

## Commands

- Run full extraction -> classification -> storage pipeline: `python main.py <CHANNEL_ID>`
  (CHANNEL_ID starts with `UC...`, not the @handle). With no argument, falls back to a default
  channel ID hardcoded in `main.py`. Classification is skipped if
  `data/comentarios_classificados.json` already exists; pass `--reclassify` to force redoing it.
- Resume/retry classification only (if `main.py` was interrupted mid-classification):
  `python finish_classification.py`
- Query/inspect what's already in ChromaDB: `python analisar.py`
- Populate/update the engagement decay engine's SQLite DB from `./data/`:
  `python build_engagement_state.py [--base-weight 20] [--db ./engagement.db] [--rebuild]`
- Run the decay engine dashboard: `streamlit run app.py`
- Run all tests: `pytest test_scoring_engine.py test_decay_engine.py -v`
- Run a single test: `pytest test_decay_engine.py::TestHalfLife -v` (or any `-k EXPR`)

There is no lint/format/typecheck tooling configured in this repo.

## Pipeline data flow (extraction/classification/storage)

Three sequential stages, each writing an intermediate file to `./data/` so the pipeline is
resumable and doesn't re-spend quota on reruns:

1. **Extraction** (`youtube_extractor.py`) — lists all channel videos via `playlistItems.list`
   (1 quota unit, not `search.list`'s 100), pulls top-level comments + replies via
   `commentThreads.list`. Writes `data/comentarios_brutos.json`. **If that file already exists,
   extraction is skipped entirely** — delete it to force re-extraction from the API. YouTube API
   calls retry transient `HttpError`s up to 3 times with short backoff (1s/2s/4s);
   `commentsDisabled` returns an empty list immediately, `quotaExceeded` aborts the run without
   retry. Mid-run progress is checkpointed per-video (append-only) to
   `data/comentarios_brutos.checkpoint.jsonl` + `data/comentarios_brutos.progress.json`, so a
   crash resumes from the next unprocessed video instead of restarting; both files are deleted
   on a clean finish. Leftover checkpoint/progress files (e.g. from a crash you don't intend to
   resume) should be deleted along with `comentarios_brutos.json` to force a full re-extraction.
2. **Classification** (`classifier.py`) — sends comments to Groq (`llama-3.1-8b-instant`) in
   batches of 50, with an explicit `max_tokens` cap sized to the batch to avoid silent JSON
   truncation, asking for `categorias` (one or more of 8 fixed labels, validated against the
   fixed list — anything else falls back to `sem_conteudo_classificavel`) + `score_engajamento`
   (0.0-1.0). Before hitting the LLM: `quick_classify()` resolves obvious cases locally
   (emoji-only, "kkk", short thank-yous — with a negation guard so "não, obrigado" isn't
   misclassified as a thank-you), and near-duplicate comments (after normalizing repeated
   chars/whitespace) are deduped to one representative per group, with the result fanned back
   out to the whole group. Checkpoints to `data/comentarios_classificados_checkpoint.json` after
   every batch so a crash/rate-limit doesn't lose progress; deleted on success. Writes
   `data/comentarios_classificados.json`. Rotates between `GROQ_API_KEY` / `GROQ_API_KEY_2` on
   429s, with exponential backoff. `main.py` skips this stage entirely if
   `data/comentarios_classificados.json` already exists (pass `--reclassify` to force it).
3. **Storage** (`storage.py`) — embeds with a multilingual SentenceTransformer model
   (`paraphrase-multilingual-MiniLM-L12-v2`, chosen because comments are mostly Portuguese and
   Chroma's default embedder is English-tuned) and upserts into a local ChromaDB collection at
   `./chroma_db/`.

The 8 engagement categories (a comment can have multiple): `agradecimento`, `elogio_generico`,
`contribuicao_valor`, `pergunta_duvida`, `critica_construtiva`, `critica_vazia`,
`spam_irrelevante`, `sem_conteudo_classificavel`.

## Engagement decay engine (super-fan churn detection)

A separate subsystem from the pipeline above, sharing only the classified comment data as
input. Models each comment author's engagement as physical decay of an "energy" value, with 5
psychological levels (L1-L5) and hysteresis on level transitions.

- **Model**: `N(t) = N0 * e^(-lambda*dt)`, `lambda = ln(2) / half_life`. Half-life depends on
  the author's current energy bucket: 7d for L1/L2 (energy 0-35), 15d for L3 (35-60), 30d for
  L4/L5 (60-100). Each new engagement event adds `delta_E = Q * base_weight` (Q in [0,1] from an
  `EngagementScorer`), clamped to [0, 100].
- **Hysteresis**: level goes up immediately on crossing the upper boundary, but only goes down
  once energy falls `HYSTERESIS_MARGIN` (3 points) *below* the lower boundary — prevents
  oscillation from noise right at a threshold. Level boundaries: 15 / 35 / 60 / 85.
- **Files**: `schema.sql` (SQLite tables `authors` / `engagement_events` /
  `author_engagement_state`), `decay_engine.py` (the math, hysteresis, batch replay,
  persistence), `scoring_engine.py` (`EngagementScorer` interface — see below),
  `build_engagement_state.py` (CLI that populates `engagement.db` from `./data/`), `app.py`
  (Streamlit dashboard). Tests in `test_decay_engine.py` and `test_scoring_engine.py`.
- **Scoring without spending LLM calls**: `CategoryWeightedScorer` reuses the Groq
  classification already on disk (`data/comentarios_classificados.json`) — no new LLM calls. If
  that file doesn't exist, `build_engagement_state.py` falls back to `HeuristicScorer` (local
  text heuristics: length, lexical diversity, question bonus) — resistant to self-promo text
  ("confira meu canal", "link na bio", etc., scored to 0) and to mass-duplicated text (same long
  text repeated 3+ times in a batch, with or without emoji/accent variation, is treated as spam
  and zeroed).
- **Incremental updates**: `build_engagement_state.py` is incremental by default — it loads the
  existing `author_engagement_state`, finds the max `published_at` already in
  `engagement_events`, and only processes comments newer than that, continuing each known
  author's state from where it left off. `--rebuild` deletes the DB and reprocesses everything.
- **Vectorization constraint (do not relax without a strong reason)**: no row-by-row Python
  loops over comments (no `iterrows` or equivalent) — everything is vectorized with
  pandas/numpy. The only Python-level loop allowed in the engine is over *calendar days with at
  least one event* during batch replay (`backfill_history`), never over individual rows. This is
  what lets it scale to a channel's full comment history without blowing up memory or runtime.
- **Current parameters are not calibrated against real business outcomes** (half-lives,
  `base_weight=20`, 3-point hysteresis margin) — reasonable starting points, but validating them
  against real retention/churn data is open future work.
- **Known limitation**: SQLite is fine for a single-channel batch job but wouldn't hold up as a
  concurrent/multi-tenant service (would need Postgres). No API layer exists yet — CLI + a
  read-only dashboard only.

## Local data/artifacts (gitignored, not committed)

`venv/`, `data/`, `engagement.db`, `chroma_db/`, `.env` are all gitignored. `data/` holds
pipeline intermediates (including the extraction/classification checkpoint files); deleting a
file there forces that pipeline stage to redo its work on the next `main.py` run. `engagement.db`
is rebuilt by `build_engagement_state.py` from `data/`.

## API quota/rate-limit notes

- YouTube Data API: default 10,000 units/day; `commentThreads.list` costs 1 unit/call.
- Groq free tier has a requests/minute cap; `classifier.py` sleeps ~1s between batches and
  rotates to `GROQ_API_KEY_2` on 429s.
