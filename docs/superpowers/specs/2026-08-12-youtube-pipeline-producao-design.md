# Design: Robustez de produção do pipeline YouTube

Data: 2026-08-12
Status: aprovado

## Contexto

Pipeline extração -> classificação -> storage (`youtube_extractor.py`, `classifier.py`,
`storage.py`, `main.py`, `finish_classification.py`) roda manualmente, com acompanhamento de
output no terminal. Code review identificou gaps que impedem uso confiável em produção:
crash mid-run perde todo progresso de extração, reruns reclassificam tudo à toa, bug de
AttributeError em erros HTTP não padrão, risco de truncamento silencioso de resposta do LLM,
e nenhum teste cobre extractor/classifier/storage.

Escopo desta mudança: só arquivos do pipeline YouTube. Não toca no engine de decay/scoring
(outro trabalho em andamento em paralelo).

## 1. Checkpoint de extração (resume mid-run)

Problema: `extract_channel_comments` acumula tudo em memória e só grava
`data/comentarios_brutos.json` no final (`youtube_extractor.py:153-155`). Crash no vídeo 400/1000
perde tudo, rerun reextrai do zero e regasta cota da API.

Solução: checkpoint append-only.

- Durante a extração, cada vídeo processado grava sua lista de comentários como uma linha JSON
  em `data/comentarios_brutos.checkpoint.jsonl` (append, sem reescrever o arquivo inteiro).
- `data/comentarios_brutos.progress.json` guarda a lista de `video_id`s já processados.
- Ao (re)iniciar: se o checkpoint existir, carrega `progress.json`, pula vídeos já feitos,
  continua appendando no jsonl a partir do próximo vídeo da lista.
- Ao terminar todos os vídeos com sucesso: concatena o jsonl inteiro no formato final
  `comentarios_brutos.json` (lista JSON, mesmo formato de hoje — zero mudança downstream),
  apaga checkpoint + progress. Mesmo padrão de "sucesso limpa checkpoint" já usado em
  `classifier.py`.
- Se `quotaExceeded` for levantado, o checkpoint parcial já escrito fica no disco (nada a mais
  a fazer, já que é append-only) — próxima run resume dali.

## 2. Retry + error handling na extração

- Fix do bug: `HttpError.error_details` pode vir como `str` OU `list[dict]` dependendo do
  corpo do erro. Tratar os dois casos antes de indexar `[0]`.
- Retry curto (3 tentativas, backoff 1s/2s/4s) para erros transitórios — `HttpError` genérico
  (não `commentsDisabled`/`quotaExceeded`) e erros de rede/timeout — em
  `get_comments_for_video`, `get_all_video_ids` e `get_uploads_playlist_id` (hoje as duas
  últimas não têm nenhum tratamento de erro).
- `commentsDisabled` continua retorno vazio imediato (não é erro).
- `quotaExceeded` continua abortando a run inteira sem retry (não adianta tentar de novo),
  mas agora o checkpoint parcial já está salvo em disco (ver seção 1).
- Se um vídeo esgota as tentativas de retry, loga aviso e segue pro próximo vídeo — não
  derruba a run inteira.

## 3. Skip-guard + robustez na classificação

- `main.py`: Etapa 2 ganha guarda análoga à Etapa 1 — se
  `data/comentarios_classificados.json` já existe, pula classificação (a menos que o usuário
  passe uma flag `--reclassify`). Hoje um rerun após sucesso reclassifica tudo do zero,
  gastando cota Groq à toa.
- `classifier.py`: valida `categorias` retornada pelo LLM contra a lista fixa `CATEGORIES` —
  categoria inválida/alucinada é substituída por `sem_conteudo_classificavel` em vez de aceita
  como está.
- Reduz risco de truncamento silencioso de JSON: `BATCH_SIZE` default cai de 100 para 50, e a
  chamada à Groq passa `max_tokens` explícito dimensionado para o batch. Hoje um lote de 100
  comentários pode gerar resposta maior que o limite padrão do modelo, truncar o JSON, e todo o
  lote (+ todos os comentários dos grupos de dedup que ele representa) vira silenciosamente
  `sem_conteudo_classificavel`/score 0.0, indistinguível de baixo valor real.
- Remove código morto: `final_results = {}` (`classifier.py:151`, atribuído e nunca lido).
- `quick_classify`: regra de `agradecimento` ganha checagem simples de negação — comentários
  como "não, obrigado" não caem mais na regra rápida (vão pro LLM em vez de virar
  `agradecimento` errado).

## 4. Storage: cache do embedding model

- `get_collection()` hoje recria `SentenceTransformerEmbeddingFunction` (recarrega o modelo do
  disco) a cada chamada — caro especialmente em `query_similar`, que recarrega o modelo a cada
  consulta. Fix: client e embedding function viram singleton em nível de módulo, carregados uma
  vez e reusados por `store_comments` e `query_similar`.

## 5. Testes (mockados, sem chamada real de API)

Seguindo o padrão já usado em `test_decay_engine.py` (pytest + mocks).

- `test_youtube_extractor.py`: paginação de `playlistItems`/`commentThreads`,
  `commentsDisabled` retorna lista vazia, `quotaExceeded` propaga a exceção e preserva o
  checkpoint parcial, retry esgota tentativas e desiste, resume pula vídeos já presentes no
  checkpoint, `error_details` tratado tanto como `str` quanto como `list`.
- `test_classifier.py`: `quick_classify` (emoji-only, "kkk", agradecimento, negação de
  agradecimento), dedup agrupa comentários e propaga fallback corretamente pro grupo inteiro,
  categoria inválida retornada pelo LLM cai pro default, checkpoint resume ignora comentários
  já classificados, skip-guard de `main.py` não reclassifica se o arquivo final já existe.
- `test_storage.py`: metadata monta corretamente (`categorias` como string join, `is_reply`
  booleano), client/embedding function não são recriados em chamadas repetidas (singleton).

## Fora de escopo

- Engine de decay/scoring (`decay_engine.py`, `scoring_engine.py`, `schema.sql`,
  `build_engagement_state.py`, `app.py`) — outro trabalho em andamento em paralelo, não tocar.
- Incremental entre runs (só pegar comentários novos desde a última coleta) — descartado nesta
  rodada, foco é checkpoint dentro da mesma run.
- Execução agendada/cron, logging estruturado, alertas — pipeline continua de disparo manual
  com acompanhamento via terminal.

## Commits

Nenhum commit será feito sem validação explícita do usuário ao final das mudanças importantes.
