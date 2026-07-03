"""DCA 멱등성용 파일 기반 상태 저장.

같은 날 중복 매수를 막기 위해 이미 집행한 clientOrderId를 기록한다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("toss.state")


class State:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self.dca_done: set[str] = set()       # 멱등성: 이미 집행한 clientOrderId
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self.dca_done = set(data.get("dca_done", []))
        except Exception as exc:
            logger.warning("상태 파일 로드 실패(%s), 새로 시작: %s", self._path, exc)

    def save(self) -> None:
        self._path.write_text(json.dumps({
            "dca_done": sorted(self.dca_done),
        }, ensure_ascii=False, indent=2))

    def is_dca_done(self, client_order_id: str) -> bool:
        return client_order_id in self.dca_done

    def mark_dca_done(self, client_order_id: str) -> None:
        self.dca_done.add(client_order_id)
