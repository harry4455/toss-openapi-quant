"""텔레그램 알림 전송. 기존 프로젝트와 동일한 env 컨벤션 사용.

  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger("toss.notifier")


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout: int = 10):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout

    def send(self, text: str) -> None:
        try:
            resp = requests.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except Exception as exc:  # 알림 실패가 봇 전체를 죽이지 않도록
            logger.error("텔레그램 전송 실패: %s", exc)


class ConsoleNotifier:
    """텔레그램 미설정 시 폴백 — 콘솔로만 출력 (드라이런/로컬 테스트용)."""

    def send(self, text: str) -> None:
        print("\n[ALERT]\n" + text + "\n")
