"""
Батч-оценка релевантности новых объявлений текстовому уточнению пользователя
через Gemini Flash (структурированный JSON-ответ через response_schema, не
свободный текст). Большие списки бьются на батчи по BATCH_SIZE — иначе
можно упереться и в лимит вывода, и в лимит запросов/минуту бесплатного
тарифа Gemini.
"""

import time

from google import genai
from google.genai import types
from pydantic import BaseModel

import config
from notify import console_log

client = genai.Client(api_key=config.GEMINI_API_KEY)

BATCH_SIZE = 15
# Пауза между батчами (сек) — бесплатный тариф Gemini ограничен по запросам
# в минуту; без паузы «Переоценить» на почти полной таблице (100 записей =
# 7 батчей) может упереться в этот лимит.
BATCH_DELAY_SEC = 1.5


class ListingVerdict(BaseModel):
    id: str
    relevant: bool
    reason: str


class EvaluationResult(BaseModel):
    results: list[ListingVerdict]


def evaluate_listings(items: list[dict], query_text: str) -> dict[str, dict]:
    """Возвращает {id: {"relevant": bool, "reason": str}} для каждого id из
    items. При ошибке API возвращает {} для соответствующего батча — те
    карточки останутся неоценёнными и будут обработаны в следующем цикле.
    Список бьётся на батчи по BATCH_SIZE, чтобы не упереться в лимиты."""
    if not items:
        return {}

    verdicts: dict[str, dict] = {}
    for start in range(0, len(items), BATCH_SIZE):
        if start > 0:
            time.sleep(BATCH_DELAY_SEC)
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
        "(1 предложение, на русском) объясни решение. Верни вердикт для "
        "всех id без исключения.\n\n"
        + "\n---\n".join(listing_blocks)
    )

    try:
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=EvaluationResult,
                max_output_tokens=4096,
            ),
        )
    except Exception as e:
        console_log(f"Ошибка вызова агента релевантности: {e}")
        return {}

    finish_reason = None
    if response.candidates:
        finish_reason = getattr(response.candidates[0], "finish_reason", None)
    if getattr(finish_reason, "name", None) == "MAX_TOKENS":
        console_log(
            f"Предупреждение: ответ агента обрезан по max_tokens "
            f"(батч из {len(items)} объявлений) — часть оценок могла не дойти"
        )

    try:
        parsed = response.parsed
    except Exception as e:
        console_log(f"Не удалось разобрать ответ агента: {e}")
        return {}

    if parsed is None:
        return {}

    return {v.id: {"relevant": v.relevant, "reason": v.reason} for v in parsed.results}
