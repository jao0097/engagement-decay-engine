# Pipeline de comentários do YouTube

Extrai todos os comentários de um canal do YouTube, classifica cada um por tipo de
engajamento usando um LLM open-source (via Groq, gratuito) e guarda tudo no
ChromaDB para análise posterior.

## Setup

1. Instale as dependências:
   ```
   pip install -r requirements.txt
   ```

2. Copie `.env.example` para `.env` e preencha as chaves:
   ```
   cp .env.example .env
   ```

   - **YOUTUBE_API_KEY**: crie em [console.cloud.google.com](https://console.cloud.google.com)
     → "APIs e serviços" → "Credenciais" → "Criar credenciais" → "Chave de API".
     Antes, ative a "YouTube Data API v3" na biblioteca de APIs do projeto.
   - **GROQ_API_KEY**: crie em [console.groq.com/keys](https://console.groq.com/keys)
     (gratuito, sem cartão de crédito).

## Uso

```
python main.py <CHANNEL_ID>
```

O `CHANNEL_ID` começa com `UC...`. Para descobrir o ID a partir do @handle do canal,
use [commentpicker.com/youtube-channel-id.php](https://commentpicker.com/youtube-channel-id.php).

O script roda em 3 etapas, salvando arquivos intermediários em `./data/`:

1. **Extração** — baixa todos os comentários (e respostas) de todos os vídeos do
   canal e salva em `data/comentarios_brutos.json`. Se esse arquivo já existir,
   a extração é pulada (apague o arquivo se quiser refazer).
2. **Classificação** — manda os comentários em lotes para o modelo Llama 3.3 70B
   via Groq, que devolve categorias + um score de engajamento (0 a 1). Resultado
   salvo em `data/comentarios_classificados.json`.
3. **Armazenamento** — insere tudo no ChromaDB (pasta `./chroma_db/`), com
   embeddings multilíngues, prontos para busca semântica e filtros por metadado.

## Categorias de engajamento

- `agradecimento` — agradece, sem mais conteúdo
- `elogio_generico` — elogia sem agregar informação
- `contribuicao_valor` — adiciona informação, correção ou dica nova
- `pergunta_duvida` — pergunta ou pede esclarecimento
- `critica_construtiva` — crítica com argumento
- `critica_vazia` — crítica ou hate sem argumento
- `spam_irrelevante` — propaganda ou fora de contexto
- `sem_conteudo_classificavel` — emojis isolados, "kkk", etc.

Um comentário pode ter mais de uma categoria.

## Analisando os dados

```
python analisar.py
```

Mostra a distribuição de categorias e as contribuições de maior valor. O arquivo
`storage.py` tem a função `query_similar(texto, where=...)` para busca semântica
com filtros, por exemplo:

```python
from storage import query_similar

query_similar(
    "problema de áudio no vídeo",
    where={"video_id": "ID_DO_VIDEO_AQUI"}
)
```

## Limites a saber

- A YouTube Data API tem cota gratuita de 10.000 unidades/dia; cada chamada de
  `commentThreads.list` custa só 1 unidade, então um canal médio cabe tranquilo.
- O tier gratuito da Groq tem limite de requisições por minuto — o script já
  espera ~2.5s entre lotes para respeitar isso. Se o canal tiver muitos
  comentários, a classificação pode demorar, mas não vai quebrar.
