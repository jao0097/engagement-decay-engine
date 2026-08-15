"""
Classifica comentarios em categorias de engajamento usando a API da Groq
(modelos open-source, tier gratuito, sem cartao de credito).

Estrategia:
- Processa em lotes (batches) para economizar chamadas e respeitar rate limits.
- Pede saida em JSON estruturado.
- Faz retry com backoff exponencial em caso de erro 429 (rate limit).
"""

import json
import re
import time
import os
from collections import defaultdict

from groq import Groq

CHECKPOINT_PATH = "./data/comentarios_classificados_checkpoint.json"

EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF]+"
)


def quick_classify(text: str) -> list[str] | None:
    """Retorna a categoria se for um caso óbvio, ou None se precisar do LLM."""
    limpo = text.strip().lower()

    # só emoji ou repetição de risada/pontuação
    sem_emoji = EMOJI_PATTERN.sub("", limpo).strip()
    if len(sem_emoji) <= 2 or re.fullmatch(r"(k|h|s|rs|haha)+[\s!.]*", sem_emoji):
        return ["sem_conteudo_classificavel"]

    # agradecimento puro e curto, sem pergunta e sem negacao
    if len(limpo.split()) <= 6 and "?" not in limpo:
        tem_negacao = re.search(r"\bn[ãa]o\b", limpo)
        if re.search(r"\bobrigad|gratid[aã]o|valeu\b", limpo) and not tem_negacao:
            return ["agradecimento"]

    return None  # caso ambíguo, manda pro LLM


def normalizar(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)  # "kkkkkk" -> "kk", "!!!!" -> "!!"
    t = re.sub(r"\s+", " ", t)
    return t
MODEL = "llama-3.1-8b-instant"
# 20: tier gratuito da Groq limita 6000 tokens/min p/ este modelo; lotes de 50
# pediam ~8000 tokens (prompt+reserva) e estouravam o limite em toda chamada,
# nao so em pico de trafego - reduzir o retry nao resolve, so lote menor resolve.
BATCH_SIZE = 20
MAX_TOKENS_PER_BATCH = 4096
BATCH_SLEEP_SECONDS = 65.0

CATEGORIES = [
    "agradecimento",
    "elogio_generico",
    "contribuicao_valor",
    "pergunta_duvida",
    "critica_construtiva",
    "critica_vazia",
    "spam_irrelevante",
    "sem_conteudo_classificavel",
]


def _validate_categorias(categorias: list[str]) -> list[str]:
    """Mantem so categorias conhecidas; se nenhuma sobrar, cai no default."""
    validas = [c for c in categorias if c in CATEGORIES]
    return validas if validas else ["sem_conteudo_classificavel"]


# Se o LLM falhar ou não retornar score, forçamos um valor coerente com o default.
# Mas vamos ajustar o SYSTEM_PROMPT para ser ainda mais rígido.
SYSTEM_PROMPT = f"""Voce e um classificador de comentarios de YouTube.

Categorias possiveis (um comentario pode ter mais de uma):
- agradecimento: agradece ou demonstra gratidao (score: 0.1 a 0.3)
- elogio_generico: elogia sem agregar informacao nova (score: 0.2 a 0.4)
- contribuicao_valor: adiciona informacao, correcao, dica ou contexto novo ao video (score: 0.6 a 1.0)
- pergunta_duvida: faz uma pergunta ou pede esclarecimento (score: 0.4 a 0.7)
- critica_construtiva: critica com argumento ou sugestao de melhoria (score: 0.5 a 0.8)
- critica_vazia: critica ou hate sem argumento (score: 0.0 a 0.2)
- spam_irrelevante: propaganda, spam ou totalmente fora de contexto (score: 0.0)
- sem_conteudo_classificavel: emojis isolados, "kkk", texto sem sentido classificavel (score: 0.0)

Para cada comentario, responda obrigatoriamente com:
- categorias: lista de uma ou mais categorias da lista acima
- score_engajamento: um float de 0.0 a 1.0. Voce DEVE incluir este campo.

Responda APENAS com um JSON valido no formato:
{{"resultados": [{{"id": "<id do comentario>", "categorias": [...], "score_engajamento": 0.0}}, ...]}}
"""


def _build_user_prompt(batch: list[dict]) -> str:
    items = [{"id": c["comment_id"], "texto": c["text"]} for c in batch]
    return "Classifique estes comentarios:\n" + json.dumps(items, ensure_ascii=False)


def classify_batch(client: Groq, batch: list[dict], max_retries: int = 5) -> dict[str, dict]:
    """Classifica um lote de comentarios. Retorna {comment_id: {categorias, score_engajamento}}."""
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                response_format={"type": "json_object"},
                max_tokens=MAX_TOKENS_PER_BATCH,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(batch)},
                ],
                temperature=0,
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)
            return {r["id"]: r for r in parsed.get("resultados", [])}

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower():
                wait = 2 ** attempt
                print(f"  [rate limit] aguardando {wait}s antes de tentar de novo...")
                time.sleep(wait)
                continue
            print(f"  [erro] falha ao classificar lote: {e}")
            return {}

    print("  [erro] numero maximo de tentativas excedido para este lote.")
    return {}


def classify_comments(api_keys: list[str], comments: list[dict]) -> list[dict]:
    """Classifica todos os comentarios em lotes, com suporte a checkpoint para continuar de onde parou."""
    classified = []

    # Carrega progresso anterior se houver
    classified_ids = set()
    if os.path.exists(CHECKPOINT_PATH):
        try:
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                classified = json.load(f)
            classified_ids = {c["comment_id"] for c in classified}
            print(f"Progresso anterior carregado: {len(classified)} comentarios ja classificados.")
        except Exception as e:
            print(f"Erro ao carregar checkpoint, iniciando do zero: {e}")
            classified = []

    # Filtra apenas o que ainda nao foi classificado
    to_classify = [c for c in comments if c["comment_id"] not in classified_ids]

    if not to_classify:
        print("Todos os comentarios ja foram classificados no checkpoint anterior!")
        return classified

    # Cria clientes Groq apenas se ha trabalho a fazer
    clients = [Groq(api_key=key) for key in api_keys]
    current_client_idx = 0

    # Nova logica: quick_classify + Deduplicacao
    print(f"Processando {len(to_classify)} comentarios novos...")

    # 1. Quick Classify
    remaining_for_llm = []
    
    for c in to_classify:
        quick = quick_classify(c["text"])
        if quick:
            c["categorias"] = quick
            c["score_engajamento"] = 0.0
            classified.append(c)
        else:
            remaining_for_llm.append(c)
            
    print(f"  - {len(to_classify) - len(remaining_for_llm)} resolvidos por regras simples.")
    print(f"  - {len(remaining_for_llm)} serao enviados ao LLM.")
    
    # 2. Deduplicacao
    groups = defaultdict(list)
    for c in remaining_for_llm:
        norm = normalizar(c["text"])
        groups[norm].append(c)
        
    representatives = []
    for norm, group in groups.items():
        representatives.append(group[0]) # Primeiro eh o representante
        
    print(f"  - {len(representatives)} grupos unicos apos dedup.")

    total_batches = (len(representatives) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(representatives), BATCH_SIZE):
        batch = representatives[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        
        # Alerta a cada 10 lotes
        if batch_num % 10 == 0:
            print(f"ALERTA: Processados {batch_num} lotes de {total_batches} ({100*batch_num/total_batches:.1f}%)")

        print(f"Classificando lote {batch_num}/{total_batches} usando chave {current_client_idx+1}...")

        # Rotacionar cliente em caso de erro 429 na proxima iteracao
        results = classify_batch(clients[current_client_idx], batch)
        
        # Se falhou, tenta rotacionar
        if not results and len(clients) > 1:
            current_client_idx = (current_client_idx + 1) % len(clients)
            print(f"  [rotação] Tentando chave {current_client_idx+1} devido a erro.")
            results = classify_batch(clients[current_client_idx], batch)

        # Propaga para todos do grupo
        for rep in batch:
            result = results.get(rep["comment_id"])
            norm = normalizar(rep["text"])

            for c in groups[norm]:
                if result and "score_engajamento" in result:
                    c["categorias"] = _validate_categorias(result.get("categorias", []))
                    c["score_engajamento"] = float(result.get("score_engajamento", 0.0))
                elif result:
                    c["categorias"] = _validate_categorias(result.get("categorias", []))
                    c["score_engajamento"] = 0.2
                else:
                    c["categorias"] = ["sem_conteudo_classificavel"]
                    c["score_engajamento"] = 0.0
                classified.append(c)

        # Salva o checkpoint em disco a cada lote processado
        try:
            os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
            with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
                json.dump(classified, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [aviso] erro ao salvar checkpoint: {e}")

        # janela do limite de 6000 tokens/min da Groq reseta por minuto;
        # esperar so 1s enfileirava lotes na mesma janela e voltava a estourar o TPM.
        time.sleep(BATCH_SLEEP_SECONDS)

    # Remove o checkpoint ao terminar com sucesso
    if os.path.exists(CHECKPOINT_PATH):
        try:
            os.remove(CHECKPOINT_PATH)
        except:
            pass

    return classified
