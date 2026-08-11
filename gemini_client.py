import logging
import asyncio
import re
from datetime import datetime
from typing import List
from google import genai
from google.genai import types

from storage import check_token_limit, add_token_usage, get_global_settings

def clean_markdown(text: str) -> str:
    """Очищает текст от символов разметки Markdown, сохраняя структуру отступов."""
    text = re.sub(r'#+\s*', '', text)
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'_+', '', text)
    text = re.sub(r'~+', '', text)
    return text

class GeminiWrapper:
    # Исправлена модель на рабочую gemini-2.5-flash
    def __init__(self, api_key: str, model_name: str = "gemini-3.6-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.google_search_tool = types.Tool(google_search=types.GoogleSearch())

    async def generate(self, prompt: str, is_scheduled: bool = False) -> str:
        can_proceed, error_msg = check_token_limit(is_scheduled=is_scheduled)
        if not can_proceed:
            raise RuntimeError(error_msg)

        settings = get_global_settings()
        master_prompt = settings.get("master_prompt", "").strip()

        config_kwargs = {"tools": [self.google_search_tool]}
        if master_prompt:
            config_kwargs["system_instruction"] = master_prompt

        search_config = types.GenerateContentConfig(**config_kwargs)

        now = datetime.now()
        current_date_str = now.strftime("%d.%m.%Y")
        contextual_prompt = (
            f"ТЕКУЩАЯ ТОЧНАЯ СЕГОДНЯШНЯЯ ДАТА: {current_date_str}.\n\n"
            f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{prompt}"
        )

        def _record_tokens(response):
            if response and hasattr(response, "usage_metadata") and response.usage_metadata:
                total_tokens = getattr(response.usage_metadata, "total_token_count", 0) or 0
                if total_tokens > 0:
                    add_token_usage(total_tokens, is_scheduled=is_scheduled)

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contextual_prompt,
                config=search_config
            )
            _record_tokens(response)
            return clean_markdown(response.text or "Пустой ответ от Gemini API.")
        except Exception as e:
            error_str = str(e)

            if "дневной лимит" in error_str or "Зарезервировано" in error_str:
                raise e

            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                logging.warning("Превышен лимит веб-поиска (429). Выполнение без поиска...")
                try:
                    fallback_config = types.GenerateContentConfig(
                        system_instruction=master_prompt if master_prompt else None
                    )
                    response = await self.client.aio.models.generate_content(
                        model=self.model_name,
                        contents=contextual_prompt,
                        config=fallback_config
                    )
                    _record_tokens(response)
                    return clean_markdown(response.text or "Пустой ответ от Gemini API.")
                except Exception as e2:
                    logging.error(f"Ошибка при генерации без поиска: {e2}")
                    raise RuntimeError(f"Не удалось получить ответ от Gemini: {e2}")

            if "503" in error_str or "UNAVAILABLE" in error_str:
                for attempt in range(2):
                    await asyncio.sleep(2)
                    try:
                        response = await self.client.aio.models.generate_content(
                            model=self.model_name,
                            contents=contextual_prompt,
                            config=search_config
                        )
                        _record_tokens(response)
                        return clean_markdown(response.text or "Пустой ответ от Gemini API.")
                    except Exception:
                        continue

            logging.error(f"Ошибка Gemini API: {e}")
            raise RuntimeError(f"Не удалось получить ответ от Gemini: {e}")

def split_message(text: str, max_length: int = 4000) -> List[str]:
    """Безопасно разбивает длинный текст на части."""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    while text:
        if len(text) <= max_length:
            parts.append(text)
            break
        
        split_at = text.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = text.rfind(" ", 0, max_length)
            if split_at <= 0:
                split_at = max_length
        
        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()
        
    return parts
