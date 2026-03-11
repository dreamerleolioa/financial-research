"""FinMind token 自動刷新管理器。

優先使用帳密（FINMIND_USER_ID + FINMIND_PASSWORD）自動登入取得當日有效 token；
若無帳密則 fallback 至 FINMIND_API_TOKEN（向後相容）。

Token 在記憶體中快取，遇到 402 時自動重新登入一次。
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_FINMIND_LOGIN_URL = "https://api.finmindtrade.com/api/v4/login"

# 模組層級單例
_manager: "FinMindTokenManager | None" = None
_lock = threading.Lock()


class FinMindTokenManager:
    """Thread-safe FinMind token manager with auto-refresh."""

    def __init__(self, user_id: str, password: str, static_token: str = "") -> None:
        self._user_id = user_id
        self._password = password
        self._static_token = static_token
        self._token: str = static_token
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def token(self) -> str:
        """取得有效的 token（若無帳密則回傳靜態 token）。"""
        if not self._user_id or not self._password:
            return self._static_token
        with self._lock:
            if not self._token:
                self._refresh()
            return self._token

    def invalidate(self) -> None:
        """強制下次使用時重新登入（遇到 402 時呼叫）。"""
        with self._lock:
            self._token = ""
            self._fetched_at = 0.0

    def _refresh(self) -> None:
        """呼叫 FinMind login API 取得新 token（需在 _lock 內呼叫）。"""
        try:
            import requests
        except ImportError as e:
            raise RuntimeError("requests 套件未安裝") from e

        logger.info("[FinMindTokenManager] 登入取得新 token（user_id=%s）", self._user_id)
        resp = requests.post(
            _FINMIND_LOGIN_URL,
            data={"user_id": self._user_id, "password": self._password},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        token = body.get("token", "")
        if not token:
            raise RuntimeError(f"FinMind 登入回傳無 token，回應：{body}")
        self._token = token
        self._fetched_at = time.time()
        logger.info("[FinMindTokenManager] token 刷新成功")


def get_token_manager() -> FinMindTokenManager:
    """取得模組層級的 FinMindTokenManager 單例。"""
    global _manager
    if _manager is None:
        with _lock:
            if _manager is None:
                _manager = FinMindTokenManager(
                    user_id=os.environ.get("FINMIND_USER_ID", ""),
                    password=os.environ.get("FINMIND_PASSWORD", ""),
                    static_token=os.environ.get("FINMIND_API_TOKEN", ""),
                )
    return _manager


def get_finmind_token() -> str:
    """一行取得有效 token 的便利函式。"""
    return get_token_manager().token
