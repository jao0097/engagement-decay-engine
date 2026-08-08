# Engagement Decay Engine

Extrai comentários do YouTube, classifica o tipo de engajamento (usando um LLM via Groq) e persiste resultados no ChromaDB para análises, queries semânticas e estudos de "engagement decay" (detecção de churn de superfãs).

Principais casos de uso

- Analisar qualidade e tipo de engajamento em canais do YouTube.
- Encontrar comentários de alto valor (dicas, correções, contribuições).
- Monitorar perda de engajamento de usuários ao longo do tempo (super-fan churn).
- Fazer buscas semânticas por comentários relevantes a um problema/tema.

Status

- Linguagem: Python
- Persistência local do ChromaDB em `./chroma_db/`
- Arquivos intermediários em `./data/`

Requisitos

- Python 3.10+
- Acesso à internet para APIs do YouTube e Groq

Instalação rápida

1. Clone o repositório:

```bash
git clone https://github.com/jao0097/engagement-decay-engine.git
cd engagement-decay-engine
```

2. Crie um virtualenv e instale dependências:

```bash
python -m venv .venv
source .venv/bin/activate  # macOS / Linux
.\.venv\Scripts\activate   # Windows (PowerShell)
pip install -r requirements.txt
```

Configuração (variáveis de ambiente)

1. Copie o template e preencha as chaves:

```bash
cp .env.example .env
```

2. Variáveis necessárias:

- YOUTUBE_API_KEY — crie no Google Cloud (ativar "YouTube Data API v3").
- GROQ_API_KEY — crie em https://console.groq.com/keys (tier gratuito disponível).

Fluxo do pipeline

O pipeline principal executa 3 etapas sequenciais e grava arquivos intermediários em `./data/`:

1. Extração
   - Recupera todos os vídeos do canal e extrai os comentários (threads e respostas).
   - Salva em `data/comentarios_brutos.json`.
   - Se esse arquivo existir, a extração é pulada (apague-o para reprocessar).

2. Classificação
   - Envia comentários em lotes ao LLM (via Groq) e recebe categorias + score de engajamento (0.0–1.0).
   - Resultado salvo em `data/comentarios_classificados.json`.
   - O pipeline já inclui um delay (~2.5s) entre lotes para respeitar limites do tier gratuito da Groq.

3. Armazenamento
   - Cria embeddings multilíngues e insere os documentos no ChromaDB local (`./chroma_db/`).
   - Há suporte a metadados para filtragem (video_id, author, published_at, etc.).

Como rodar

- Executar o pipeline completo:

```bash
python main.py <CHANNEL_ID>
```

- Análise / queries locais:

```bash
python analisar.py
```

- Exemplo de uso programático (busca semântica):

```python
from storage import query_similar

results = query_similar(
    "problema de áudio no vídeo",
    where={"video_id": "ID_DO_VIDEO_AQUI"},
    top_k=10
)
```

Categorias de engajamento (conversão do LLM)

- agradecimento — agradece, sem mais conteúdo
- elogio_generico — elogia sem agregar informação
- contribuicao_valor — adiciona informação, correção ou dica nova
- pergunta_duvida — pergunta ou pede esclarecimento
- critica_construtiva — crítica com argumento
- critica_vazia — crítica ou hate sem argumento
- spam_irrelevante — propaganda ou fora de contexto
- sem_conteudo_classificavel — emojis isolados, "kkk", etc.

Observação: um comentário pode ter múltiplas categorias.

Limitações e notas operacionais

- YouTube Data API tem cota (por padrão 10.000 unidades/dia). Chamadas a `commentThreads.list` custam 1 unidade.
- Groq (tier gratuito) impõe limite de requests/minuto — o script usa delay entre lotes; canais grandes podem demorar a classificar.
- Dados intermediários em `./data/`. Remover arquivos nessa pasta força reprocessamento daquela etapa.
- ChromaDB é persistido localmente em `./chroma_db/`.

Estrutura de arquivos (resumo)

- main.py — pipeline orchestration (extração → classificação → armazenamento)
- analisar.py — scripts de análise e visualização simples
- storage.py — interface com ChromaDB, criação de embeddings, queries
- youtube_extractor.py — lógica para listar vídeos e extrair comentários
- groq_client.py — wrapper para chamadas ao endpoint Groq
- requirements.txt — dependências

Boas práticas para rodar em canais grandes

- Faça testes com canais pequenos/menos ativos para validar a configuração.
- Aumente o batch_size/timeout com cautela; respeite os limites de API.
- Monitore uso de cota no Google Cloud Console e a taxa de requests da Groq.

Contribuindo

- Abra issues para bugs, melhorias ou ideias de features.
- PRs são bem-vindos — prefira commits pequenos e testes quando aplicável.

Licença

Este repositório não contém um arquivo LICENSE; adicione-o se desejar tornar o projeto open-source com uma licença explícita.

Agradecimentos

- Groq — por disponibilizar acesso a LLMs no tier gratuito
- ChromaDB — para indexação e busca semântica local
