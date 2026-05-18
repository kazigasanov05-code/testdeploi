"""
Меры защиты ботов и базы данных: валидация ввода, лимиты, безопасный путь к БД.
"""
import re
import os
from pathlib import Path

# Максимальная длина полей (защита от переполнения и злоупотреблений)
MAX_CATEGORY_LEN = 100
MAX_NAME_LEN = 200
MAX_VOLUME_LEN = 50
MAX_PRICE_LEN = 20
MAX_CALLBACK_DATA_LEN = 64  # Лимит Telegram

# Разрешённые символы для категории и названия (буквы, цифры, пробелы, дефис, апостроф)
SAFE_TEXT_PATTERN = re.compile(r"^[\w\s\-'.,()]+$", re.UNICODE)


def sanitize_text(text: str, max_len: int = 500) -> str:
    """Обрезает до max_len и убирает лишние пробелы."""
    if not text or not isinstance(text, str):
        return ""
    return text.strip()[:max_len]


def validate_category(category: str) -> tuple[bool, str]:
    """Проверка категории. Возвращает (ok, error_message)."""
    category = sanitize_text(category, MAX_CATEGORY_LEN)
    if not category:
        return False, "Категория не может быть пустой"
    if len(category) > MAX_CATEGORY_LEN:
        return False, f"Слишком длинное название (макс. {MAX_CATEGORY_LEN} символов)"
    return True, ""


def validate_menu_name(name: str) -> tuple[bool, str]:
    """Проверка названия напитка."""
    name = sanitize_text(name, MAX_NAME_LEN)
    if not name:
        return False, "Название не может быть пустым"
    if len(name) > MAX_NAME_LEN:
        return False, f"Слишком длинное название (макс. {MAX_NAME_LEN} символов)"
    return True, ""


def validate_volume(volume: str) -> tuple[bool, str]:
    """Проверка объёма."""
    volume = sanitize_text(volume, MAX_VOLUME_LEN)
    if not volume:
        return True, ""  # объём может быть пустым
    if len(volume) > MAX_VOLUME_LEN:
        return False, f"Слишком длинное значение (макс. {MAX_VOLUME_LEN})"
    return True, ""


def validate_price(price_str: str) -> tuple[bool, str]:
    """Проверка цены: только цифры и опционально 'р' в конце."""
    price_str = sanitize_text(price_str, MAX_PRICE_LEN)
    if not price_str:
        return False, "Введите цену"
    if len(price_str) > MAX_PRICE_LEN:
        return False, f"Слишком длинное значение (макс. {MAX_PRICE_LEN})"
    digits = "".join(filter(str.isdigit, price_str))
    if not digits:
        return False, "Цена должна содержать цифры"
    if int(digits) > 10**8:
        return False, "Недопустимая сумма"
    return True, ""


def safe_db_path(base_path: str) -> str:
    """
    Возвращает безопасный путь к файлу БД: без выхода за пределы директории.
    На хостинге лучше задавать DB_PATH в переменных окружения (например /data/coffee_menu.db).
    """
    path = Path(base_path).resolve()
    # Запрет на путь типа /etc/passwd и т.п. — только имя файла или явный абсолютный путь в допустимой папке
    if path.is_absolute() and ".." not in base_path:
        return str(path)
    # Относительный путь — храним в директории проекта
    root = Path(__file__).resolve().parent
    return str((root / path.name).resolve())