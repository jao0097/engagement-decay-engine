# Sprint de coleta omnichannel (YouTube + Reddit + Instagram) para modelo de decaimento

## Contexto

O motor de decaimento de engajamento (`decay_engine.py`) hoje só é alimentado
pelo pipeline de comentários do YouTube de um único canal (`main.py` ->
`classifier.py` -> `storage.py` -> `build_engagement_state.py`). Para validar
o modelo matemático (`N(t) = N0 * e^(-lambda*dt)`) contra um dataset mais
amplo e multicanal, precisamos de uma coleta rápida (sprint de 4h, orçamento
$0) que junte dados públicos de YouTube, Reddit e Instagram num único CSV
normalizado, pronto pra análise exploratória.

Este é um subsistema novo e independente do pipeline existente — não
reaproveita `youtube_extractor.py`/`classifier.py`/`storage.py` diretamente
(aqueles extraem *comentários* de um canal via client lib; este extrai
*métricas de posts/vídeos* de múltiplas plataformas via `requests` puro), mas
segue as mesmas convenções do repo: staging em `./data/`, `.env` +
`python-dotenv`, logs de progresso, try/except que loga e segue sem quebrar o
fluxo.

## Objetivo

3 scripts sequenciais + README, executáveis sem retoque manual:

1. `coleta_sprint_4h.py` — coleta concorrente das 3 plataformas, aceita
   qualquer canal/perfil/subreddit informado pelo usuário (ID, URL ou
   handle), grava `dados_brutos.csv`.
2. `consolidar_dados.py` — normaliza pro schema unificado, calcula métricas
   derivadas, grava `dados_consolidados_omnichannel.csv`.
3. `analise_exploratoria.py` — estatísticas descritivas + histogramas +
   relatório texto.
4. `README.md` — setup e ordem de execução.

## Fora de escopo

- Autenticação Reddit via OAuth (client_id/secret) — usa-se o endpoint
  público `www.reddit.com/r/{sub}/hot.json` (só `User-Agent`, sem
  credencial), por restrição de orçamento zero.
- Login/sessão autenticada no Instagram — scraping é só de HTML/JSON público;
  perfis privados ou que exigirem login retornam falha tratada (log + skip),
  não é objetivo contornar isso.
- Persistência em banco (ChromaDB/SQLite) — saída é só CSV, análise é
  offline via pandas/matplotlib.
- Integração com `decay_engine.py`/`build_engagement_state.py` — este sprint
  entrega só o dataset consolidado; plugar no motor de decaimento é trabalho
  futuro separado.

## Arquitetura

### 1. `coleta_sprint_4h.py`

**Input flexível via CLI** (`argparse`), todos os parâmetros opcionais:

```
python coleta_sprint_4h.py \
  --youtube UC_x,https://youtube.com/@handle,UC_y \
  --reddit r/python,dataisbeautiful,https://reddit.com/r/technology \
  --instagram nasa,@natgeo,https://instagram.com/spacex/
```

Sem args: usa listas default de 2-3 canais/perfis/subreddits públicos e
genéricos (ex.: canais de tech/ciência no YouTube, `r/dataisbeautiful` +
`r/technology` + `r/programming` no Reddit, contas institucionais como
`nasa`/`natgeo` no Instagram), com log avisando que são valores de teste.

**Normalização de targets** — cada plataforma tem uma função
`normalizar_<plataforma>_target(raw: str) -> str` que aceita ID/username cru,
URL completa ou handle, e devolve a forma canônica usada nas chamadas:

- YouTube: `UC...` direto passa; `youtube.com/channel/UC...` extrai o ID;
  `youtube.com/@handle` ou `@handle` cru é resolvido pra `channel_id` via
  `channels.list?forHandle=` (1 unidade de cota) antes de seguir.
- Reddit: `nome`, `r/nome` ou `reddit.com/r/nome` -> `nome`.
- Instagram: `user`, `@user` ou `instagram.com/user/` -> `user`.

**Concorrência**: `ThreadPoolExecutor(max_workers=3)`, uma thread por
plataforma. Cada thread só acumula numa lista local própria (sem estado
compartilhado entre threads, sem lock necessário); resultados são coletados
via `as_completed`, e uma exceção não tratada dentro de uma thread é
capturada no laço principal — falha de uma plataforma não derruba as outras
nem perde os dados já coletados.

**YouTube** (`requests` puro, sem `googleapiclient`):
- Por canal: `playlistItems.list` (1 unidade/chamada) pra listar IDs de
  vídeo da playlist de uploads, depois `videos.list?part=statistics,snippet`
  em lotes de até 50 IDs por chamada (mais barato em cota que 1 chamada por
  vídeo).
- Meta: 200-500 vídeos de 2-3 canais.
- Rate limit: `time.sleep(0.05)` entre chamadas.
- Erros HTTP (quota excedida, canal inválido) por canal: log + `continue`
  pro próximo canal.

**Reddit** (`requests` puro, endpoint público):
- `GET https://www.reddit.com/r/{sub}/hot.json?limit=100&after={token}`,
  header `User-Agent` customizado (identifica o script, evita 429 genérico).
- Paginação via `after` até atingir a meta ou acabarem os posts.
- Meta: 100-200 posts de 3-5 subreddits.
- Rate limit: throttle pra ficar abaixo de 60 req/min (`time.sleep` entre
  chamadas calculado pra isso).
- Erros HTTP por subreddit: log + `continue`.

**Instagram** (`requests` + `BeautifulSoup`):
- `GET https://www.instagram.com/{username}/`, `User-Agent` de navegador
  real, delay aleatório `random.uniform(1.0, 3.0)` entre perfis.
- Parse do JSON embutido no HTML (`<script type="application/ld+json">` ou
  `window.__additionalDataLoaded`/`_sharedData`, o que estiver presente) via
  BeautifulSoup + `json.loads`.
- Meta: 100-150 posts de 2-3 perfis.
- Resiliência: qualquer falha (429/403, mudança de estrutura HTML, JSON
  ausente/malformado) é capturada, logada, e o script segue pro próximo
  perfil sem interromper a coleta de YouTube/Reddit (que já rodaram/rodam em
  threads separadas).

**Saída**: `data/dados_brutos.csv` — schema solto (colunas específicas por
plataforma + coluna comum `platform`), um CSV só com todas as linhas de
todas as plataformas empilhadas (`pd.concat`).

**Logging**: biblioteca `logging`, nível INFO, progresso em tempo real
(quantos itens coletados por plataforma, avisos de erro/skip).

### 2. `consolidar_dados.py`

- Carrega `data/dados_brutos.csv`.
- Normaliza estritamente pro schema unificado (13 colunas, tipos exatos
  conforme especificado pelo usuário):
  `platform, content_id, title, channel_title, username, subreddit,
  created_at, likes, comments, views, engagement_total,
  hours_since_publish`.
- `title` truncado em 280 chars; `channel_title`/`username`/`subreddit`
  vazios (`""`) quando não aplicável à plataforma da linha.
- `likes`/`comments`/`views`: `NaN` -> `0`, tipo `int`.
- `engagement_total = likes + comments * 2`.
- `hours_since_publish`: `(datetime.now(timezone.utc) - created_at).total_seconds() / 3600`,
  com `created_at` parseado de forma tolerante (aceita variações de formato
  ISO 8601 entre as 3 plataformas).
- Dedup por `content_id` (`drop_duplicates`).
- Validação de schema antes de salvar (colunas esperadas presentes, tipos
  batendo) — se algo não bate, log de erro claro em vez de salvar CSV
  quebrado.
- Saída: `data/dados_consolidados_omnichannel.csv`.

### 3. `analise_exploratoria.py`

- Carrega `data/dados_consolidados_omnichannel.csv`.
- `groupby("platform")[["likes","comments","views","engagement_total"]].agg(["mean","median","std"])`.
- Bucketiza `hours_since_publish` em `[0-24h]`, `[1-7 dias]`, `[7+ dias]`,
  conta posts por bucket x plataforma.
- `matplotlib`: grid de histogramas (1 subplot por plataforma, distribuição
  de `engagement_total`) -> salva `histogramas.png`.
- Escreve `relatorio_exploratorio.txt` com as estatísticas descritivas e a
  distribuição temporal, formatado pra leitura humana.

### 4. `README.md`

- Pré-requisitos (`pip install requests pandas beautifulsoup4 lxml
  matplotlib python-dotenv`, adicionados a `requirements.txt`).
- Como obter `YOUTUBE_API_KEY` (reaproveita instruções já existentes no
  README principal do repo).
- Ordem exata de execução dos 3 scripts, com exemplos de `--youtube
  --reddit --instagram` custom e sem args (defaults de teste).

## Tratamento de erros (regra geral)

Toda chamada de rede e todo parse de data/string tem `try/except`
específico, loga via `logging` e segue o fluxo (nunca deixa uma exceção não
tratada derrubar o processo inteiro). Instagram é o ponto mais frágil
(estrutura pode mudar sem aviso) — sua falha nunca compromete os dados já
coletados de YouTube/Reddit, que ficam em memória (listas separadas por
thread) até serem persistidos juntos no final.

## Testes

Sprint de 4h não comporta suíte de testes formal nova; validação é manual
via execução ponta a ponta com os defaults de teste e inspeção dos 4
artefatos gerados (`dados_brutos.csv`, `dados_consolidados_omnichannel.csv`,
`histogramas.png`, `relatorio_exploratorio.txt`).
