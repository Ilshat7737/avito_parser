"""
Батч-оценка релевантности новых объявлений текстовому уточнению пользователя
через Claude Haiku 4.5 (tool use — структурированный ответ, не свободный текст).
Большие списки бьются на батчи по BATCH_SIZE, чтобы ответ не обрезался по max_tokens.
"""

import anthropic

import config
from notify import console_log

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

BATCH_SIZE = 15

EVALUATE_TOOL = {
    "name": "submit_relevance",
    "description": "Вернуть релевантность и краткое обоснование для каждого объявления из списка.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "relevant": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "relevant", "reason"],
                },
            }
        },
        "required": ["results"],
    },
}


def evaluate_listings(items: list[dict], query_text: str) -> dict[str, dict]:
    """Возвращает {id: {"relevant": bool, "reason": str}} для каждого id из
    items. При ошибке API возвращает {} для соответствующего батча — те
    карточки останутся неоценёнными и будут обработаны в следующем цикле.
    Список бьётся на батчи по BATCH_SIZE, чтобы не упереться в max_tokens."""
    if not items:
        return {}

    verdicts: dict[str, dict] = {}
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start:start + BATCH_SIZE]
        verdicts.update(_evaluate_batch(batch, query_text))
    return verdicts


def _evaluate_batch(items: list[dict], query_text: str) -> dict[str, dict]:
    listing_blocks = []
    for item in items:
        listing_blocks.append(
            f"id={item['id']}\n"
            f"Заголовок: {item['title']}\n"
            f"Цена: {item.get('price')} ₽\n"
            f"Год: {item.get('year')}\n"
            f"Пробег: {item.get('mileage')} км\n"
            f"Описание: {item.get('description') or ''}"
        )

    prompt = (
        f"Критерий пользователя: {query_text}\n\n"
        "Ниже список объявлений о продаже машин. Для КАЖДОГО id из списка "
        "определи, соответствует ли оно критерию пользователя, и коротко "
        "(1 предложение, на русском) объясни решение. Верни результат "
        "вызовом инструмента submit_relevance для всех id без исключения.\n\n"
        + "\n---\n".join(listing_blocks)
    )

    try:
        response = client.messages.create(
            model=config.AGENT_MODEL,
            max_tokens=4096,
            tools=[EVALUATE_TOOL],
            tool_choice={"type": "tool", "name": "submit_relevance"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        console_log(f"Ошибка вызова агента релевантности: {e}")
        return {}

    if getattr(response, "stop_reason", None) == "max_tokens":
        console_log(
            f"Предупреждение: ответ агента обрезан по max_tokens "
            f"(батч из {len(items)} объявлений) — часть оценок могла не дойти"
        )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_relevance":
            results = block.input.get("results", [])
            verdicts = {}
            for r in results:
                try:
                    verdicts[r["id"]] = {"relevant": r["relevant"], "reason": r["reason"]}
                except (KeyError, TypeError):
                    continue
            return verdicts
    return {}
