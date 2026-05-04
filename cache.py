"""
Двойное кеширование для отслеживания изменений.
Хранит два кеша: старый (previous) и новый (current).
"""

import json
import os
from typing import Dict, Any, List, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

CACHE_FILE = os.environ.get("CACHE_FILE", "refund_cache.json")
TIMEZONE = ZoneInfo("Asia/Tashkent")


def load_cache() -> Dict[str, Any]:
    """Загружает кеш из файла."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "previous": [],  # старый кеш
        "current": [],   # новый кеш
        "last_check": None
    }


def save_cache(data: Dict[str, Any]) -> None:
    """Сохраняет кеш в файл."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def row_key(r: dict) -> str:
    """Создает уникальный ключ для строки."""
    return f"{r.get('order_id')}|{r.get('pvz')}|{r.get('status', '')}"


def get_changes(new_rows: List[Dict]) -> Dict[str, List]:
    """
    Сравнивает новые данные с кешем и возвращает изменения.

    Returns:
        {
            "added": [...],      # новые записи
            "removed": [...],    # удаленные записи
            "modified": [(old, new), ...]  # измененные записи
        }
    """
    cache = load_cache()

    # Первый запуск - все записи новые
    if not cache["current"]:
        cache["previous"] = []
        cache["current"] = new_rows
        cache["last_check"] = datetime.now(TIMEZONE).isoformat()
        save_cache(cache)
        return {
            "added": new_rows,
            "removed": [],
            "modified": []
        }

    # Сдвигаем кеши: current -> previous, new_rows -> current
    old_rows = cache["current"]
    cache["previous"] = old_rows
    cache["current"] = new_rows
    cache["last_check"] = datetime.now(TIMEZONE).isoformat()
    save_cache(cache)

    # Создаем словари для быстрого поиска
    old_dict = {row_key(r): r for r in old_rows}
    new_dict = {row_key(r): r for r in new_rows}

    old_keys = set(old_dict.keys())
    new_keys = set(new_dict.keys())

    # Находим изменения
    added = [new_dict[k] for k in (new_keys - old_keys)]
    removed = [old_dict[k] for k in (old_keys - new_keys)]

    # Находим измененные (одинаковый order_id+pvz, но разный статус)
    modified = []
    common_keys = old_keys & new_keys
    for k in common_keys:
        old_r = old_dict[k]
        new_r = new_dict[k]
        # Проверяем изменился ли статус или другие поля
        if (old_r.get("status") != new_r.get("status") or
            old_r.get("reason") != new_r.get("reason") or
            old_r.get("amount") != new_r.get("amount")):
            modified.append((old_r, new_r))

    return {
        "added": added,
        "removed": removed,
        "modified": modified
    }


def clear_cache() -> None:
    """Полностью очищает кеш."""
    save_cache({
        "previous": [],
        "current": [],
        "last_check": None
    })
