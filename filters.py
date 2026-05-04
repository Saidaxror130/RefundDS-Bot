"""
Фильтрация данных по нашим ПВЗ или ТУ (Николаева В.)
"""

# Наши ПВЗ
OUR_PVZ = {
    "ТАШ-3", "ТАШ-5", "ТАШ-8", "ТАШ-27", "ТАШ-29", "ТАШ-50",
    "ТАШ-52", "ТАШ-65", "ТАШ-79", "ТАШ-82", "ТАШ-90", "ТАШ-93",
    "ТАШ-98", "ТАШ-100", "ТАШ-107", "ТАШ-151", "ТАШ-146",
    "FrТАШ-168", "FrТАШ-183", "FrТАШ-185", "FrТАШ-187", "FrТАШ_205",
    "FrТАШ-225", "FrТАШ-255", "FrТАШ-296", "FrТАШ-310", "FrТАШ-313"
}

# Наш ТУ
OUR_TU = "Николаева В."


def normalize_pvz(name: str) -> str:
    """Нормализует название ПВЗ."""
    return (
        name.upper()
        .replace("TAШ", "ТАШ")
        .replace("ТAШ", "ТАШ")
        .replace("FRТАШ", "FRТАШ")
        .replace("FRTАШ", "FRТАШ")
        .strip()
    )


OUR_PVZ_NORMALIZED = {normalize_pvz(p) for p in OUR_PVZ}


def is_our_pvz(pvz_name: str) -> bool:
    """Проверяет, является ли ПВЗ нашим."""
    return normalize_pvz(pvz_name) in OUR_PVZ_NORMALIZED


def filter_our_data(rows: list) -> list:
    """
    Фильтрует данные по нашим ПВЗ.
    Можно также фильтровать по ТУ, но фильтрация по ПВЗ более точная.
    """
    return [r for r in rows if is_our_pvz(r.get("pvz", ""))]
