import os
import logging
import asyncio
from datetime import datetime
from typing import List
from google import genai
from google.genai import types
from aiogram import Bot
from aiogram.methods import SendRichMessage
from aiogram.types import InputRichMessage

from storage import check_token_limit, add_token_usage, get_global_settings
from formatter import TextFormatter

class GeminiWrapper:
    def __init__(self, api_key: str, model_name: str = None):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name or "gemini-3.6-flash"
        self.google_search_tool = types.Tool(google_search=types.GoogleSearch())
        self.formatter = TextFormatter()

    def _extract_grounding_sources(self, response) -> str:
        """Извлекает реальные проверенные ссылки из метаданных поиска Google."""
        try:
            if not response or not response.candidates:
                return ""
            
            candidate = response.candidates[0]
            metadata = getattr(candidate, "grounding_metadata", None)
            if not metadata:
                return ""

            chunks = getattr(metadata, "grounding_chunks", [])
            if not chunks:
                return ""

            sources = []
            seen_uris = set()

            for chunk in chunks:
                web = getattr(chunk, "web", None)
                if web:
                    uri = getattr(web, "uri", None)
                    title = getattr(web, "title", None) or uri
                    if uri and uri not in seen_uris:
                        seen_uris.add(uri)
                        sources.append(f"• [{title}]({uri})")

            if sources:
                return "\n\n🔗 **Первоисточники из Google Search:**\n" + "\n".join(sources[:7])
        except Exception as e:
            logging.warning(f"⚠️ Не удалось извлечь grounding sources: {e}")
        return ""

    async def generate(self, prompt: str, is_scheduled: bool = False) -> str:
        """Отправляет запрос в Gemini API, а затем красиво форматирует результат."""
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
        current_date_str = now.strftime("%d.%m.%Y (%A)")
        contextual_prompt = (
            f"ТЕКУЩАЯ ТОЧНАЯ СЕГОДНЯШНЯЯ ДАТА: {current_date_str}.\n\n"
            f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{prompt}"
        )

        def _record_tokens(response):
            if response and hasattr(response, "usage_metadata") and response.usage_metadata:
                total_tokens = getattr(response.usage_metadata, "total_token_count", 0) or 0
                if total_tokens > 0:
                    add_token_usage(total_tokens, is_scheduled=is_scheduled)

        raw_result = ""

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contextual_prompt,
                config=search_config
            )
            _record_tokens(response)
            raw_result = response.text or "Пустой ответ от Gemini API."

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
                    raw_result = response.text or "Пустой ответ от Gemini API."
                except Exception as e2:
                    logging.error(f"Ошибка при генерации без поиска: {e2}")
                    raise RuntimeError(f"Не удалось получить ответ от Gemini: {e2}")

            elif "503" in error_str or "UNAVAILABLE" in error_str:
                for attempt in range(2):
                    await asyncio.sleep(2)
                    try:
                        response = await self.client.aio.models.generate_content(
                            model=self.model_name,
                            contents=contextual_prompt,
                            config=search_config
                        )
                        _record_tokens(response)
                        raw_result = response.text or "Пустой ответ от Gemini API."
                        break
                    except Exception:
                        continue
            else:
                logging.error(f"Ошибка Gemini API: {e}")
                raise RuntimeError(f"Не удалось получить ответ от Gemini: {e}")

        # Стадия переформатирования
        if raw_result:
            formatted_result = await self.formatter.reformat(raw_result)
            return formatted_result
            
        return raw_result

def split_message(text: str, max_length: int = 30000) -> List[str]:
    """Разбивает длинный текст на части до max_length символов."""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    while text:
        if len(text) <= max_length:
            parts.append(text)
            break
        
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
            if split_at == -1:
                split_at = max_length
        
        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()
        
    return parts

async def send_rich_text(bot: Bot, chat_id: int, text: str):
    """
    Отправляет отформатированный текст через Telegram API sendRichMessage.
    Если sendRichMessage не сработал — нарезает текст по 4000 символов под лимиты sendMessage.
    """
    chunks_30k = split_message(text, max_length=30000)

    for chunk in chunks_30k:
        try:
            await bot(SendRichMessage(
                chat_id=chat_id,
                rich_message=InputRichMessage(markdown=chunk)
            ))
        except Exception as e:
            logging.warning(f"⚠️ sendRichMessage не сработал ({e!r}). Нарезаем по 4000 символов под sendMessage...")

            sub_chunks = split_message(chunk, max_length=4000)
            for sub_chunk in sub_chunks:
                try:
                    await bot.send_message(chat_id, sub_chunk, parse_mode="Markdown")
                except Exception as e2:
                    logging.warning(f"⚠️ Ошибка с разметкой Markdown ({e2}), отправка простым текстом...")
                    await bot.send_message(chat_id, sub_chunk)
