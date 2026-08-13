# AGENTS.md

## Estrutura e Fluxo
- **Pipeline**: O processo de dados segue: `Extração` -> `Classificação` -> `Armazenamento`.
- **Arquivos Intermediários**: Scripts salvam progresso em `./data/`. Se `data/comentarios_brutos.json` existir, a etapa de extração é pulada.
- **Armazenamento**: O ChromaDB é persistido localmente em `./chroma_db/`.

## Execução
- **Configuração**: Exige `.env` com `YOUTUBE_API_KEY` e `GROQ_API_KEY`.
- **Comando Principal**: `python main.py <CHANNEL_ID>` para iniciar o pipeline.
- **Análise**: `python analisar.py` executa queries no banco de dados.

## Quirks e Limites
- **API YouTube**: O script consome a cota da `commentThreads.list`.
- **Groq API**: O script inclui uma pausa de ~2.5s entre lotes para respeitar limites de requisições por minuto do tier gratuito da Groq.
- **Data Persistence**: Apagar arquivos em `./data/` força o reprocessamento da etapa correspondente.

## Motor de Decaimento de Engajamento (Super-fãs)

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

- **Arquivos**: `schema.sql` (tabelas `authors`/`engagement_events`/
  `author_engagement_state`), `decay_engine.py` (matemática de decaimento,
  histerese, replay em lote, persistência SQLite), `scoring_engine.py`
  (interface `EngagementScorer` com duas implementações), `build_engagement_state.py`
  (script que popula `engagement.db` a partir de `data/`), `app.py` (dashboard
  Streamlit). Testes em `test_decay_engine.py` e `test_scoring_engine.py`
  (47 casos, `pytest test_scoring_engine.py test_decay_engine.py -v`).

- **Adaptador WhatsApp**: `whatsapp_extractor.py` lê um export manual do
  WhatsApp (`Exportar conversa` no menu do grupo, formato Android
  `DD/MM/AAAA HH:MM - Autor: mensagem`), filtra mensagens de sistema
  (criptografia, mídia oculta, entrada/saída de membro) e pontua cada
  mensagem com `HeuristicScorer` (sem custo de LLM). Roda separado:
  `python whatsapp_extractor.py --input data/whatsapp_bruto_<grupo>.txt
  --grupo "Nome do Grupo"`, gera `data/whatsapp_eventos.json`, que
  `build_engagement_state.py` lê automaticamente se existir. Testes em
  `test_whatsapp_extractor.py`.

- **Como rodar**: `python build_engagement_state.py` popula/atualiza
  `engagement.db`, processando cada plataforma presente em `./data/`
  separadamente (cutoff incremental e `base_weight` independentes por
  plataforma — `--base-weight-youtube`/`--base-weight-whatsapp`, default 20
  pros dois). `--rebuild` força reprocessar tudo. **Mudança de schema**: bancos
  `engagement.db` criados antes desta versão não têm a coluna `platform` —
  rode com `--rebuild` uma vez após atualizar. `streamlit run app.py` abre o
  dashboard (usa `engagement.db` se existir, senão gera dados sintéticos na
  hora).

- **Scoring sem gastar LLM**: `CategoryWeightedScorer` reaproveita a
  classificação Groq já feita (`data/comentarios_classificados.json`) sem
  chamar o LLM de novo. Se esse arquivo não existir, cai para
  `HeuristicScorer` (heurísticas locais de texto — tamanho, diversidade
  lexical, pergunta) — resistente a auto-promoção ("confira meu canal",
  "link na bio" etc.) e a texto duplicado em massa (mesmo texto longo
  repetido 3+ vezes num lote, com ou sem emoji/acento, conta como spam e
  zera o score).

- **Restrição de projeto (não relaxar sem motivo forte)**: proibido laço
  Python linha-a-linha sobre comentários (`iterrows` ou equivalente) — tudo
  vetorizado com pandas/numpy. O único laço Python permitido no motor é
  sobre dias-com-evento no replay em lote (`backfill_history`), nunca sobre
  linhas individuais.

- **Parâmetros atuais (não calibrados contra outcome real de negócio)**:
  meia-vida 7d (L1/L2), 15d (L3), 30d (L4/L5); `base_weight=20`; margem de
  histerese de 3 pontos. Servem como ponto de partida razoável, mas ainda
  não foram validados contra um resultado de negócio real — calibrar isso é
  trabalho futuro em aberto.

- **Limitação conhecida**: `SQLite` funciona bem para o job em lote de um
  canal só, mas não é adequado pra virar serviço concorrente/multi-tenant
  (precisaria migrar pra Postgres nesse caso). Não existe camada de API
  ainda — hoje é só CLI + dashboard somente-leitura.
