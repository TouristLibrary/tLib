# Version 1.0 - 22.06.2026 14:25:00 GMT
# Утилита для поиска расхождений между наборами ZIP- и JSON-файлов.
# Сравнивает имена (без расширений и лидирующих нулей) во всех подпапках двух директорий.
# Выводит: «Есть JSON, нет ZIP» и «Есть ZIP, нет JSON». Требует только stdlib.

import os
import time
from pathlib import Path


def get_file_names(root_dir, ext):
    """Рекурсивно собирает стемы файлов заданного расширения, убирая лидирующие нули."""
    print(f"\nСканирую '{ext}' файлы в: {root_dir}")
    start = time.time()
    files = []
    for path, _, filenames in os.walk(root_dir):
        for file in filenames:
            if file.lower().endswith(ext):
                files.append(Path(file).stem.lstrip("0"))
    print(f"Найдено {len(files)} '{ext}' файлов за {round(time.time() - start, 2)} сек.")
    return set(files)


def main():
    zip_dir = input("Введите путь к директории с ZIP файлами: ").strip()
    json_dir = input("Введите путь к директории с JSON файлами: ").strip()

    zip_names = get_file_names(zip_dir, '.zip')
    json_names = get_file_names(json_dir, '.json')

    print("\nСравнение файлов...")
    only_json = sorted(json_names - zip_names)
    only_zip = sorted(zip_names - json_names)

    print("\n=== Есть JSON, нет ZIP ===")
    print("\n".join(only_json) if only_json else "Все JSON имеют соответствующий ZIP")

    print("\n=== Есть ZIP, нет JSON ===")
    print("\n".join(only_zip) if only_zip else "Все ZIP имеют соответствующий JSON")

    input("\nНажмите Enter для выхода...")


if __name__ == "__main__":
    main()
