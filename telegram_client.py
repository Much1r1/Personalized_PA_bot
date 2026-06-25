import httpx
import asyncio
import logging
import random
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class TelegramClient:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(10)  # Basic rate limiting: 10 concurrent requests

    async def start(self):
        if not self.client:
            self.client = httpx.AsyncClient(timeout=30.0)

    async def stop(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: Optional[int] = None,
        parse_mode: Optional[str] = "Markdown",
        reply_markup: Optional[Dict[str, Any]] = None,
        max_retries: int = 5
    ) -> Dict[str, Any]:
        """
        Sends an outbound message to a Telegram chat with error handling,
        rate-limiting, and exponential backoff.
        Returns a dictionary: {"success": bool, "response": Optional[dict], "error": Optional[str]}
        """
        if not self.client:
            await self.start()

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup:
            payload["reply_markup"] = reply_markup

        for attempt in range(max_retries):
            async with self._semaphore:
                try:
                    response = await self.client.post(url, json=payload)

                    if response.status_code == 429:
                        retry_after = response.json().get("parameters", {}).get("retry_after", 2 ** attempt)
                        logger.warning(f"Rate limited by Telegram. Retrying after {retry_after}s (Attempt {attempt+1}/{max_retries})")
                        await asyncio.sleep(retry_after)
                        continue

                    if response.status_code >= 500:
                        wait_time = (2 ** attempt) + (random.random() * 0.1)
                        logger.warning(f"Telegram Server Error {response.status_code}. Retrying after {wait_time:.2f}s")
                        await asyncio.sleep(wait_time)
                        continue

                    response_data = response.json()
                    response.raise_for_status()
                    return {"success": True, "response": response_data, "error": None}

                except httpx.HTTPStatusError as e:
                    error_msg = f"Telegram API Error: {e.response.text}"
                    logger.error(error_msg)
                    # For non-retryable errors, return failure
                    if attempt == max_retries - 1 or response.status_code < 500:
                        try:
                            resp_json = e.response.json()
                        except Exception:
                            resp_json = {"raw_error": e.response.text}
                        return {"success": False, "response": resp_json, "error": error_msg}
                except (httpx.RequestError, asyncio.TimeoutError) as e:
                    wait_time = (2 ** attempt) + (random.random() * 0.1)
                    error_msg = f"Network/Timeout error: {e}. Retrying after {wait_time:.2f}s"
                    logger.warning(error_msg)
                    if attempt == max_retries - 1:
                        return {"success": False, "response": None, "error": str(e)}
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    error_msg = f"Unexpected error sending Telegram message: {e}"
                    logger.error(error_msg)
                    return {"success": False, "response": None, "error": error_msg}

        return {"success": False, "response": None, "error": "Max retries reached"}
