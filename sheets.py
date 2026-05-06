"""
Читает данные из Google Sheets через публичный CSV-экспорт.

Структура колонок (Май 2026):
  A  - Номер заказа (нужен)
  B  - worker_id
  C  - ТУ
  D  - РУ
  E  - Дата возврата (нужен)
  F  - колл
  G  - short_name / ПВЗ (нужен)
  H  - Тип оплаты (нужен)
  I  - Сумма возврата (нужен)
  J  - Статус возврата
  K  - Клиент (нужен)
  L  - Наличие заявления
  M  - days_since_created
  N  - error_list_translated / Причина доработки (нужен)
  O  - created_timestamp
  P  - sla_status / Статус обработки (нужен)
  Q  - days_since_created_delay
"""

import csv
import io
import os
import urllib.parse
import urllib.request
import time
import logging
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

# Валидация обязательной переменной окружения
if "SHEET_NAME" not in os.environ:
    raise ValueError(
        "❌ Переменная окружения SHEET_NAME не установлена!\n"
        "Укажите название листа таблицы (например: 'Май 2026')"
    )

SHEET_NAME = os.environ["SHEET_NAME"]


def fetch_refund_rows(spreadsheet_id: str, sheet_name: str = SHEET_NAME, max_retries: int = 3) -> List[Dict]:
    """Возвращает список строк таблицы как словарей с retry механизмом."""

    url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}"
    )

    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")

            reader = csv.reader(io.StringIO(raw))
            rows_out = []

            for i, row in enumerate(reader):
                if i == 0:
                    continue   # заголовок

                if len(row) < 16:  # Минимум до колонки P
                    continue

                # Берем только нужные колонки (новая структура Май 2026)
                order_id = row[0].strip() if len(row) > 0 else ""   # A - Номер заказа
                pvz = row[6].strip() if len(row) > 6 else ""        # G - short_name (ПВЗ)
                date_refund = row[4].strip() if len(row) > 4 else "" # E - Дата возврата
                payment_type = row[7].strip() if len(row) > 7 else "" # H - Тип оплаты
                amount = row[8].strip() if len(row) > 8 else ""     # I - Сумма возврата
                client = row[10].strip() if len(row) > 10 else ""   # K - Клиент
                reason = row[13].strip() if len(row) > 13 else ""   # N - error_list_translated
                status = row[15].strip() if len(row) > 15 else ""   # P - sla_status

                if not order_id or not pvz:
                    continue

                rows_out.append({
                    "order_id": order_id,
                    "pvz": pvz,
                    "date_refund": date_refund,
                    "payment_type": payment_type,
                    "amount": amount,
                    "client": client,
                    "reason": reason,
                    "status": status,
                })

            logger.info(f"Успешно загружено {len(rows_out)} строк из таблицы")
            return rows_out

        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.warning(f"Ошибка при чтении таблицы (попытка {attempt + 1}/{max_retries}): {e}. Повтор через {wait_time}s")
                time.sleep(wait_time)
            else:
                logger.error(f"Не удалось загрузить таблицу после {max_retries} попыток: {e}")
                raise Exception(f"Ошибка загрузки таблицы после {max_retries} попыток: {e}") from last_error

    raise Exception(f"Не удалось загрузить таблицу: {last_error}")
