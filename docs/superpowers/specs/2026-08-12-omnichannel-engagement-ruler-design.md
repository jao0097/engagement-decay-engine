# Régua de engajamento universal (YouTube + WhatsApp) para o motor de decaimento

## Contexto

O motor de decaimento de engajamento (`decay_engine.py`) hoje só é alimentado
por comentários do YouTube: `build_engagement_state.py` lê
`data/comentarios_classificados.json` (ou `data/comentarios_brutos.json` como
fallback) e monta os eventos direto com nomes de campo YouTube-específicos
(`comment_id`, `author`, `video_id`). O `schema.sql` reflete isso: PK de
`authors` é `author_channel_id`, e `engagement_events.video_id`/`comment_id`
são `NOT NULL`.

O valor central do negócio é essa "régua de engajamento" — o par
energia/nível (L1-L5) por autor que já alimenta `churn_risk_report` e o
dashboard (`app.py`). Para virar uma régua útil de verdade, ela precisa
funcionar com a mesma qualidade em qualquer rede onde os clientes têm
comunidades, começando por YouTube (já existente) e WhatsApp (novo).

Comunidades atendidas são pequenas nesta fase — antispam/rajada não é
prioridade agora. O foco é a entrada do motor ser plataforma-agnóstica e a
energia continuar comparável entre plataformas com volumes de evento muito
diferentes (grupo de WhatsApp gera muito mais mensagens por autor por dia do
que comentários por vídeo no YouTube).

Não há identidade cross-platform: o mesmo humano no YouTube e no WhatsApp é
tratado como dois autores distintos, sem tentativa de matching (não há dado
disponível pra isso — ex. telefone vinculado a canal).

## Objetivo

1. Universalizar o schema de evento consumido pelo motor de decaimento, para
   que qualquer plataforma alimente o mesmo pipeline sem o motor saber de
   onde veio o dado.
2. Adicionar um adaptador WhatsApp (`whatsapp_extractor.py`) que parseia
   export manual de chat (`.txt`) e produz eventos nesse schema universal,
   pontuados por `HeuristicScorer` (sem custo de LLM).
3. Adaptar o adaptador YouTube existente (hoje embutido em
   `build_events_frame()`) pro mesmo schema.
4. Permitir `base_weight` diferente por plataforma, para que o ganho de
   energia por evento seja calibrável separadamente sem mexer na matemática
   do decaimento.

## Fora de escopo

- Matching de identidade cross-platform (mesma pessoa em duas redes).
- Qualquer mecanismo antispam/anti-rajada (cap de eventos por dia, etc.) —
  comunidades pequenas agora, não é prioridade.
- WhatsApp Business API / captura automatizada — só export manual `.txt`.
- Migração de dados de um `engagement.db` existente para o novo schema —
  arquivo é local e gitignorado, basta `--rebuild`.
- Filtro de plataforma no dashboard (`app.py`) — pode vir depois, os
  relatórios existentes (`churn_risk_report`, distribuição de nível) já
  funcionam sem alteração porque leem `author_engagement_state` agregado.
- Novas plataformas além de YouTube/WhatsApp (Reddit/Instagram do sprint
  `coleta_sprint_4h.py` continuam fora do motor de decaimento, sem mudança).

## Arquitetura

### 1. Schema de evento universal

Contrato que qualquer adaptador de plataforma deve produzir (`DataFrame` ou
lista de dicts com estas colunas):

```
event_id            texto, unico -- YouTube: comment_id. WhatsApp: hash de
                                     (author_id, published_at, texto), pois o
                                     export nao tem ID nativo de mensagem.
platform             "youtube" | "whatsapp"
author_id             identificador do autor, escopado a plataforma
                     (YouTube: @handle/author id atual. WhatsApp: nome/numero
                     como aparece no export).
author_display_name  nome pra exibicao (pode ser igual a author_id).
content_id            YouTube: video_id. WhatsApp: nome do grupo/chat.
published_at           ISO 8601 UTC.
quality_score          float [0,1] -- ja calculado na extracao/adaptacao.
categorias              string, opcional (YouTube tem, WhatsApp fica vazio).
```

### 2. `schema.sql`

- `authors`: PK vira composta `(platform, author_id)`. Coluna
  `author_channel_id` renomeada para `author_id`.
- `engagement_events`: ganha coluna `platform` (`NOT NULL`); `video_id` vira
  `content_id`; `comment_id` vira `event_source_id`. `author_channel_id` vira
  `author_id`, com FK composta `(platform, author_id) -> authors(platform,
  author_id)`.
- `author_engagement_state`: mesma mudança de PK composta.
- Índices existentes recriados sobre os novos nomes de coluna; adiciona
  índice em `platform` isolado (útil pra filtrar por rede no futuro).
- Mudança é breaking (não há migração) — documentado no README: rodar
  `build_engagement_state.py --rebuild` uma vez após atualizar.

### 3. Adaptador YouTube (`youtube_to_universal_events()`)

Extrai pra função nova em `build_engagement_state.py` (ou módulo próprio) a
lógica hoje hardcoded em `build_events_frame()`: mesma leitura de
`comentarios_classificados.json`/`comentarios_brutos.json` e mesmo cálculo de
`quality_score` (`CategoryWeightedScorer` ou `HeuristicScorer` fallback, sem
mudança de comportamento), só remapeando os nomes de coluna pro schema
universal e fixando `platform="youtube"`.

### 4. Adaptador WhatsApp (`whatsapp_extractor.py`, novo)

- **Entrada**: `./data/whatsapp_bruto_<grupo>.txt`, export manual do
  WhatsApp (menu do grupo -> Exportar conversa -> sem mídia).
- **Parsing**: regex do formato padrão Android
  `DD/MM/AAAA HH:MM - Autor: mensagem` (uma linha por mensagem; linhas sem
  esse prefixo são continuação da mensagem anterior, concatenadas). Formato
  iOS difere ligeiramente (colchetes, segundos) — fora de escopo nesta
  primeira versão, mas o parser isola a regex numa função só, fácil de
  estender depois.
- **Filtro de mensagens de sistema**: remove linhas como "As mensagens e as
  ligações são protegidas com criptografia de ponta a ponta", "<Mídia
  oculta>", entrada/saída de membro, mudança de nome/descrição do grupo —
  lista de padrões conhecidos, comparação exata/regex simples.
- **Scoring**: `HeuristicScorer.score_batch()` sobre o texto de cada
  mensagem filtrada (mesma classe já usada como fallback do YouTube — zero
  mudança na lógica de pontuação, só a fonte do texto muda).
- **Saída**: `data/whatsapp_eventos.json`, já no schema universal completo
  (incluindo `quality_score`), análogo a `comentarios_classificados.json` —
  o resto do pipeline não recalcula nada, só lê.

### 5. `build_engagement_state.py` — orquestração por plataforma

- Descobre quais arquivos de evento existem em `./data/` (YouTube via
  `comentarios_classificados.json`/`comentarios_brutos.json`, WhatsApp via
  `whatsapp_eventos.json`; ausência de um arquivo só pula aquela
  plataforma, não é erro).
- Para cada plataforma presente, em sequência:
  1. Carrega e converte pro schema universal (adaptador específico).
  2. Calcula cutoff incremental **por plataforma**:
     `SELECT MAX(published_at) FROM engagement_events WHERE platform = ?`
     (hoje é `MAX(published_at)` global — precisa ficar por-plataforma pra
     processar YouTube novo sem re-tocar o WhatsApp já gravado, e
     vice-versa).
  3. Filtra eventos novos (> cutoff daquela plataforma), insere no SQLite.
  4. Roda `decay_engine.backfill_history()` só com os eventos daquela
     plataforma, usando o `base_weight` daquela plataforma (dict
     `{"youtube": 20, "whatsapp": <valor a calibrar>}`, com
     `--base-weight-youtube`/`--base-weight-whatsapp` como overrides de
     CLI, mantendo os defaults do dict se omitidos).
  5. Salva o estado resultante em `author_engagement_state` — não colide
     com outras plataformas porque a chave já é `(platform, author_id)`.
- `decay_engine.py` em si **não muda**: `backfill_history`,
  `apply_energy_gain`, histerese, meia-vida — tudo continua recebendo um
  `base_weight` escalar por chamada, só que agora a chamada é uma vez por
  plataforma em vez de uma vez pra tudo.

## Fluxo de dados (ponta a ponta)

```
YouTube:
main.py -> comentarios_classificados.json -> youtube_to_universal_events()
                                                     |
WhatsApp:                                           v
export manual .txt -> whatsapp_extractor.py -> whatsapp_eventos.json
                                                     |
                                                     v
                          build_engagement_state.py (loop por plataforma)
                                                     |
                                                     v
                    engagement.db (authors, engagement_events,
                    author_engagement_state -- todos com platform)
                                                     |
                                                     v
                          app.py (churn_risk_report, distribuicao de nivel
                          -- sem alteracao, ja agrega sobre author_engagement_state)
```

## Testes

- `test_whatsapp_extractor.py`:
  - Parsing do formato `DD/MM/AAAA HH:MM - Autor: texto`.
  - Mensagem multi-linha (linha de continuação sem timestamp anexada à
    mensagem anterior).
  - Filtro de mensagens de sistema (criptografia, mídia oculta,
    entrada/saída de membro).
  - `event_id` determinístico (mesmo input -> mesmo hash, inputs diferentes
    -> hashes diferentes).
  - Arquivo vazio ou só com mensagens de sistema -> lista vazia, sem
    exceção.
- `test_build_engagement_state.py` (estende o existente):
  - `youtube_to_universal_events()` com fixture pequena, confere nomes de
    coluna e `platform="youtube"`.
  - Cutoff incremental por plataforma: gravar eventos YouTube, rodar de novo
    só com WhatsApp novo, confirmar que YouTube não é reprocessado (e
    vice-versa).
  - Ausência de um dos dois arquivos de origem não quebra o `main()` — só
    pula a plataforma ausente com log.
- `test_decay_engine.py` (estende o existente):
  - Dois `backfill_history()` (um por plataforma, `base_weight` diferentes)
    gravando no mesmo `author_engagement_state`; confirma que autores de
    plataformas diferentes não colidem mesmo com `author_id` igual por
    coincidência (ex. mesmo texto usado como nome em ambas), e que
    `churn_risk_report` enxerga autores de ambas as plataformas.
- Nenhum teste precisa de rede: WhatsApp é leitura de arquivo local, YouTube
  já usa os JSONs de `./data/` como hoje — tudo com fixtures.

## Erros e casos de borda

- `.txt` do WhatsApp em formato inesperado (iOS, ou export com mídia
  incluída mudando o padrão de linha): linhas que não casam com a regex
  principal nem parecem continuação são logadas como aviso e ignoradas —
  nunca derruba o parsing do arquivo inteiro.
- Grupo de WhatsApp sem nenhuma mensagem válida após filtro de sistema:
  `whatsapp_eventos.json` sai vazio, `build_engagement_state.py` loga e
  segue (mesmo padrão do YouTube quando não há comentários novos).
- `engagement.db` de uma versão anterior ao schema novo: `init_schema` usa
  `CREATE TABLE IF NOT EXISTS`, então um banco velho com o schema antigo
  quebra em runtime por coluna ausente — documentar claramente no README
  que é preciso `--rebuild` uma vez após esta mudança.
