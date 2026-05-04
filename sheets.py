"""
Читает данные из Google Sheets через публичный CSV-экспорт.

Структура колонок:
  A  - Номер заказа (нужен)
  B  - ПВЗ
  C  - Дата возврата
  D  - Тип оплаты
  E  - Сумма возврата
  F  - Клиент
  G  - Причина доработки
  H  - Статус обработки
  I  - Номер заказа (дубликат, не нужен)
  ... остальные колонки
"""

import csv
import io
import urllib.parse
import urllib.request
import time
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

SHEET_NAME = "Лист1"


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

                if len(row) < 8:
                    continue

                # Берем только нужные колонки
                order_id = row[0].strip() if len(row) > 0 else ""   # A - Номер заказа
                pvz = row[1].strip() if len(row) > 1 else ""        # B - ПВЗ
                date_refund = row[2].strip() if len(row) > 2 else "" # C - Дата возврата
                payment_type = row[3].strip() if len(row) > 3 else "" # D - Тип оплаты
                amount = row[4].strip() if len(row) > 4 else ""     # E - Сумма возврата
                client = row[5].strip() if len(row) > 5 else ""     # F - Клиент
                reason = row[6].strip() if len(row) > 6 else ""     # G - Причина доработки
                status = row[7].strip() if len(row) > 7 else ""     # H - Статус обработки
                # Колонка I (row[8]) - игнорируем, это дубликат номера заказа

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
