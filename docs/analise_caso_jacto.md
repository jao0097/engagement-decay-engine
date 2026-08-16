# Como o algoritmo funciona — estudo de caso: Jacto vs Grupo Jacto

Este documento explica os dois algoritmos do pipeline (classificação de engajamento e
decaimento de energia/super-fã) e usa uma extração real de dois canais do YouTube —
**Jacto** (produto) e **Grupo Jacto** (institucional) — como evidência de que os números
que eles produzem fazem sentido.

## 1. Classificação de engajamento (`classifier.py`)

Cada comentário é enviado a um LLM (Groq, `llama-3.1-8b-instant`) em lotes, pedindo duas
saídas: uma ou mais das 8 categorias fixas (`agradecimento`, `elogio_generico`,
`contribuicao_valor`, `pergunta_duvida`, `critica_construtiva`, `critica_vazia`,
`spam_irrelevante`, `sem_conteudo_classificavel`) e um `score_engajamento` (0.0–1.0).
Antes de gastar uma chamada de LLM, `quick_classify()` resolve localmente os casos óbvios
(emoji isolado, "kkk", agradecimento curto sem negação), e comentários quase-duplicados são
agrupados e classificados uma única vez, propagando o resultado pro grupo inteiro.

**Restrição real descoberta nesta rodada**: o tier gratuito da Groq limita 6000 tokens/min
para este modelo. Lotes de 50 comentários pediam ~8000 tokens (prompt + reserva de
completion) e estouravam o limite em praticamente toda chamada — não era pico de tráfego,
era estrutural. Corrigido reduzindo o lote pra 20 e espaçando as chamadas em 65s (janela de
reset do TPM). Antes do fix, 93,8% dos comentários do Jacto caíam no fallback
`sem_conteudo_classificavel`/score 0 por esgotamento de tentativas; depois do fix, o fallback
caiu pra 13,3% (Jacto) e 15,9% (Grupo Jacto) — taxa condizente com o que se espera de
`quick_classify` pegando emoji/spam sozinho, não de erro de infraestrutura.

### Evidência: distribuição real

| Categoria | Jacto (2621 coment.) | Grupo Jacto (88 coment.) |
|---|---|---|
| elogio_generico | 30.5% | 38.6% |
| contribuicao_valor | 29.3% | 33.0% |
| agradecimento | 25.0% | 38.6% |
| sem_conteudo_classificavel | 13.7% | 15.9% |
| pergunta_duvida | 10.2% | 8.0% |
| critica_construtiva | 7.2% | 3.4% |
| critica_vazia | 6.0% | 5.7% |
| spam_irrelevante | 3.8% | 0% |
| **score médio** | **0.408** | **0.555** |

Os números batem com o esperado por tipo de canal: Jacto é canal de produto agrícola
(vídeos técnicos de pulverizadores, plantadeiras) — atrai mais volume, mais spam e mais
crítica construtiva (dúvida técnica real). Grupo Jacto é canal institucional (RH,
saúde/segurança, história da empresa) — audiência pequena mas com score médio 36% mais alto
e zero spam, condizente com público interno/família da empresa em vez de público aberto.

## 2. Decaimento de engajamento / detecção de super-fã (`decay_engine.py`)

Modela o engajamento de cada autor como decaimento físico de uma "energia" (0-100):
`N(t) = N0 * e^(-lambda*dt)`, com `lambda = ln(2)/half_life`. O half-life depende do bucket
de energia atual do autor — 7 dias em L1/L2 (energia baixa), 15 dias em L3, 30 dias em L4/L5
(quanto mais engajado, mais devagar esfria). Cada comentário novo soma
`delta_E = score_engajamento * base_weight` (peso 20 por padrão), e a transição de nível usa
histerese de 3 pontos pra não oscilar por ruído perto de um limite (15/35/60/85).

### Evidência: os dois canais não têm super-fãs de verdade hoje

| | Jacto (1179 autores) | Grupo Jacto (74 autores) |
|---|---|---|
| L1 (energia baixa) | 1177 (99,8%) | 73 (98,6%) |
| L2+ | 2 | 1 |
| Maior energia (real, excluindo conta oficial) | @FSgame-wp2rn: 14.5 | @samuelmoreira9526: 18.0 |

Quase todo autor comentou uma única vez e nunca voltou — o modelo captura isso corretamente
como decaimento total (energia perto de zero) em vez de mostrar super-fãs artificiais.
Faz sentido: nenhum dos dois é canal de criador de conteúdo com comunidade recorrente, são
canais institucionais/B2B, onde esse padrão de engajamento pontual é esperado.

**Limitação real encontrada**: o autor de maior energia no Jacto era `@JactoBrasil` (47.1,
nível 3) — a própria conta oficial do canal respondendo comentários, não um fã. O algoritmo
hoje não distingue autor=marca de autor=fã, então esse tipo de ranking precisa filtrar contas
oficiais manualmente antes de virar decisão de negócio.

## 3. Conclusão

Os dois algoritmos (classificação por categoria/score e decaimento de energia) produziram
resultados internamente consistentes e alinhados com o tipo de cada canal (produto vs
institucional), o que é a validação disponível sem dado de retenção real — os parâmetros
(half-life, `base_weight=20`, margem de histerese) continuam sendo pontos de partida
razoáveis, não calibrados contra churn real (ver `CLAUDE.md`).

Dados usados nesta análise: `data/comentarios_classificados_jacto.json`,
`data/comentarios_classificados_grupo_jacto.json`, `data/engagement_jacto.db`,
`data/engagement_grupo_jacto.db` (gitignored, não versionados).
