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
