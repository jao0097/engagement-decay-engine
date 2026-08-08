# HeuristicScorer resistente a spam bem escrito

## Contexto

`HeuristicScorer` (`scoring_engine.py`) pontua um comentário em `Q ∈ [0,1]`
usando só tamanho normalizado, diversidade lexical e bônus de pergunta — sem
LLM. Isso o deixa vulnerável a spam bem escrito (frase longa, palavras
variadas, mas promocional ou repetida em massa), que pontua alto por engano.

Nos dados reais já processados isso não é um problema hoje: o pipeline usa
`CategoryWeightedScorer`, que herda a classificação Groq e já zera
`spam_irrelevante`. `HeuristicScorer` só entra em cena como caminho sem LLM —
fallback quando `data/comentarios_classificados.json` não existe, canal/rede
nova ainda sem classificador configurado, e geração de dados sintéticos no
dashboard (`app.py::generate_mock_events`). Mesmo assim, vale deixá-lo
robusto agora para que essa "versão base" funcione bem sozinha, sem depender
do Groq.

## Objetivo

`HeuristicScorer` deve zerar (`Q = 0.0`):
1. Comentários com padrão de auto-promoção (ex.: "confira meu canal", "link
   na bio", "me segue", "inscreva-se no meu perfil").
2. Comentários com texto longo (>40 caracteres normalizados) repetido 3+
   vezes no mesmo lote — mesmo autor (bot fazendo copy-paste) ou autores
   diferentes (campanha coordenada) — sem distinguir os dois casos.

Frases curtas e genéricas (`"top demais"`, `"muito bom"`) **não** devem ser
zeradas por repetição, mesmo aparecendo muitas vezes — são reação normal, não
spam.

## Fora de escopo

- `CategoryWeightedScorer` — já correto via Groq, não muda.
- Calibração de pesos/thresholds contra dado real de negócio (fica para
  sessão futura de fine-tuning, quando houver como validar contra outcome
  real).
- Detecção de spam via link/URL/hashtag e CAPS LOCK (descartado nesta
  rodada; pode virar spec própria depois se necessário).

## Design

### 1. Auto-promoção — dentro de `score()`

Lista de padrões regex compilados (case-insensitive), verificados via `any()`
antes do cálculo normal de score. Mantida como lista de padrões pequenos e
nomeados (não uma regex gigante), para ficar fácil de estender depois:

```python
SELF_PROMO_PATTERNS = [
    re.compile(r"(confir[ae]|visit[ae]|segu[ae]|inscrev[ae]|assist[ae]).{0,25}(meu|nosso).{0,10}(canal|perfil|instagram|insta)", re.IGNORECASE),
    re.compile(r"link\s*na\s*bio", re.IGNORECASE),
    re.compile(r"me\s+segue", re.IGNORECASE),
    re.compile(r"(clique|acesse)\s+(no|o)\s+link", re.IGNORECASE),
]
```

Em `score()`, logo após o early-return de texto vazio/risada/emoji, checa:

```python
if any(p.search(text) for p in self.SELF_PROMO_PATTERNS):
    return 0.0
```

### 2. Duplicata em lote — override de `score_batch()`

Constantes na classe: `DUP_MIN_CHARS = 40`, `DUP_MIN_COUNT = 3`.

```python
def score_batch(self, comments: list[dict]) -> list[float]:
    textos = pd.Series([(c.get("text") or "").strip().lower() for c in comments])
    normalizados = textos.str.replace(r"[^\w\s]", "", regex=True).str.strip()

    contagem = normalizados.value_counts()
    candidatos_dup = normalizados.map(contagem) >= self.DUP_MIN_COUNT
    e_longo = normalizados.str.len() > self.DUP_MIN_CHARS
    e_duplicata = (candidatos_dup & e_longo).to_numpy()

    scores = [self.score(c) for c in comments]
    return [0.0 if dup else s for dup, s in zip(e_duplicata, scores)]
```

A contagem de ocorrências é vetorizada via pandas (`value_counts` +
`map`), não um laço Python contando string a string — consistente com a
exigência de vetorização já aplicada em `decay_engine.py`. O laço restante
(`[self.score(c) for c in comments]`) já existe hoje e não muda de natureza;
só passa a ter seu resultado zerado onde há duplicata.

Isso adiciona `import pandas as pd` a `scoring_engine.py` (hoje não usa
pandas). Aceitável: pandas já é dependência direta do projeto.

### 3. Integração em `build_engagement_state.py`

Hoje `load_and_score_comments` monta `quality_score` com list comprehensions
manuais chamando `scorer.score(...)` direto, para os dois scorers — ignorando
`score_batch()`. Trocar os dois caminhos para chamar `scorer.score_batch(...)`
sobre a lista de dicts correspondente, para que a detecção de duplicata do
`HeuristicScorer` realmente dispare no pipeline real (o `CategoryWeightedScorer`
não muda de comportamento, `score_batch` dele já cai no default da ABC, que
é equivalente ao list comprehension atual).

## Testes (`test_scoring_engine.py`)

Novos casos em `TestHeuristicScorer`:
- `test_autopromocao_score_zero` — frases de cada padrão da lista → `0.0`.
- `test_texto_legitimo_com_palavra_canal_nao_e_zerado` — frase que menciona
  "canal" mas não é auto-promoção (ex.: "gostei muito do canal de vocês")
  não deve cair nos padrões (guarda contra falso positivo largo demais).
- `test_duplicata_longa_em_lote_score_zero` — mesmo texto longo (>40 chars)
  3x no lote → todas as 3 ocorrências `0.0`.
- `test_duplicata_curta_nao_e_zerada` — `"top demais"` repetido 5x no lote →
  mantém o score individual normal.
- `test_duplicata_abaixo_do_minimo_nao_e_zerada` — texto longo repetido só
  2x → não conta como spam.
- `test_score_batch_ainda_bate_com_score_individual_sem_duplicata` — lote sem
  nenhuma duplicata/spam deve dar o mesmo resultado que chamar `score()` um a
  um (garante que a mudança não altera o caminho feliz).

## Critério de sucesso

- Suíte de testes (`test_scoring_engine.py` + `test_decay_engine.py`) passa
  100%, incluindo os novos casos acima.
- `build_engagement_state.py --rebuild` roda de ponta a ponta sobre o dataset
  real sem erro (valida a troca para `score_batch`, mesmo que o caminho real
  hoje use `CategoryWeightedScorer`, não `HeuristicScorer`).
- Nenhuma mudança de comportamento no `CategoryWeightedScorer` nem no
  `decay_engine.py`.
