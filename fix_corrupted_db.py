"""
Скрипт для исправления повреждённой SQLite-базы данных.
Резервирует повреждённый файл и позволяет init_db создать новую чистую БД.
"""
import os
import shutil
from pathlib import Path

try:
    import config
    DB_PATH = config.DB_PATH
except ImportError:
    DB_PATH = os.environ.get("DB_PATH", "coffee_menu.db")

# Преобразуем в абсолютный путь
db_path = Path(DB_PATH)
if not db_path.is_absolute():
    db_path = Path.cwd() / db_path

if not db_path.exists():
    print(f"Файл БД не найден: {db_path}")
    print("Ничего делать не нужно — при первом запуске бота БД создастся автоматически.")
    exit(0)

backup_path = db_path.with_suffix(db_path.suffix + ".corrupted.backup")
counter = 1
while backup_path.exists():
    backup_path = db_path.with_name(db_path.stem + f".corrupted.backup.{counter}" + db_path.suffix)
    counter += 1

try:
    shutil.move(str(db_path), str(backup_path))
    print(f"Повреждённая БД сохранена в : {backup_path}")
except Exception as e:
    print(f"Ошибка при резервном копировании: {e}")
    print("Попробуйте вручную удалить или переименовать файл:", db_path)
    exit(1)
