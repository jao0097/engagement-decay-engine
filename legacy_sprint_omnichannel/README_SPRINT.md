> **Arquivado**: Reddit/Instagram foram descartados (scraping nao confiavel, revertido).
> O motor de decaimento hoje ingere YouTube + WhatsApp (ver `whatsapp_extractor.py`
> e `build_engagement_state.py` na raiz). Mantido so como referencia historica.

# Sprint Omnichannel — Coleta de Engajamento (YouTube + Reddit + Instagram)

Pipeline de 3 scripts, orcamento $0, para coletar dados publicos de
engajamento de 3 plataformas, consolidar num schema unico e gerar analise
exploratoria — insumo para o modelo de decaimento de engajamento
(`decay_engine.py`) validar contra um dataset multicanal.

## Pre-requisitos

- Python 3.10+
- Dependencias (ja inclusas em `requirements.txt` do projeto):

```bash
pip install -r requirements.txt
```

## Configuracao

Precisa apenas da chave da YouTube Data API v3 (Reddit e Instagram nao
exigem credencial nesta coleta — endpoints publicos/scraping anonimo).

1. Acesse https://console.cloud.google.com/apis/credentials
2. Crie um projeto (ou use um existente) e ative "YouTube Data API v3" em
   "APIs & Services" -> "Library"
3. Em "Credentials", crie uma "API Key"
4. Adicione no `.env` da raiz do projeto:

```
YOUTUBE_API_KEY=sua_chave_aqui
```

## Execucao

Rode os 3 scripts nesta ordem:

```bash
# 1. Coleta (aceita ID/URL/handle de canal, subreddit, ou perfil do Instagram)
python coleta_sprint_4h.py \
  --youtube UC_x,https://youtube.com/@handle \
  --reddit r/python,dataisbeautiful,technology \
  --instagram nasa,@natgeo

# sem argumentos, usa alvos default de teste (canais/perfis/subreddits publicos genericos)
python coleta_sprint_4h.py

# 2. Consolidacao
python consolidar_dados.py

# 3. Analise exploratoria
python analise_exploratoria.py
```

## Saidas

- `data/dados_brutos.csv` — dados crus, um esquema por linha por plataforma
- `data/dados_consolidados_omnichannel.csv` — schema unificado (12 colunas:
  platform, content_id, title, channel_title, username, subreddit,
  created_at, likes, comments, views, engagement_total, hours_since_publish)
- `histogramas.png` — distribuicao de engagement_total por plataforma
- `relatorio_exploratorio.txt` — estatisticas descritivas + distribuicao
  temporal (0-24h / 1-7 dias / 7+ dias)

## Limitacoes conhecidas

- Instagram: scraping anonimo depende de uma estrutura de HTML
  (`window._sharedData`) que a Instagram pode remover ou bloquear sem
  aviso; quando isso acontece, o perfil e' pulado (log de aviso) e o
  restante da coleta segue normalmente. Nao ha fallback autenticado —
  esta fora do escopo do sprint (orcamento $0, sem login).
- Reddit usa o endpoint publico `www.reddit.com/.../hot.json`, sem OAuth —
  respeita rate limit por `User-Agent` fixo e `sleep` entre paginas, mas
  pode ser bloqueado se o IP fizer muitas requisicoes em pouco tempo.
- YouTube Data API tem cota diaria (10.000 unidades); este script usa
  `playlistItems.list` + `videos.list` em lote, mais barato que 1 chamada
  por video, mas ainda consome cota do projeto configurado em
  `YOUTUBE_API_KEY`.
