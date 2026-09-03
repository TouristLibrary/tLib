# Version 1.0 - 22.06.2026 14:25:00 GMT
# Утилита для тестирования кодировок кириллицы.
# Создаёт текстовые файлы с каждой русской буквой (А-Я, а-я) в разных кодировках,
# чтобы проверить корректность чтения/записи кириллицы в целевой среде.
# Требует только stdlib.

import os


def get_russian_letters():
    """Возвращает список кортежей (буква, регистр)."""
    uppercase = [(chr(code), 'U') for code in range(1040, 1072)]  # А-Я
    lowercase = [(chr(code), 'L') for code in range(1072, 1104)]  # а-я
    return uppercase + lowercase


def get_encodings():
    """Возвращает список кодировок, безопасных для записи кириллицы."""
    return [
        'utf-8',
        'utf-16',
        'utf-16le',
        'utf-16be',
        'utf-32',
        'utf-32le',
        'utf-32be',
        'cp1251',
        'koi8-r',
        'iso8859_5',
        'mac_cyrillic',
    ]


def safe_filename(*parts):
    """Создаёт имя файла из указанных частей."""
    return '-'.join(parts) + '.txt'


def create_file(encoding, register, letter, content):
    """Создаёт файл с текстом в указанной кодировке."""
    filename = safe_filename(encoding, register, letter)
    try:
        with open(filename, 'w', encoding=encoding, errors='strict') as f:
            f.write(content)
    except Exception as e:
        print(f"Ошибка для файла '{filename}' с кодировкой {encoding}: {e}")


def generate_files(content):
    """Генерирует файлы со всеми буквами во всех кодировках."""
    for encoding in get_encodings():
        for letter, register in get_russian_letters():
            create_file(encoding, register, letter, content)


def main():
    try:
        print("Введите текст, который должен быть записан в каждый файл:")
        content = input()
        generate_files(content)
        print("Файлы успешно созданы.")
    except Exception as e:
        print(f"Произошла ошибка: {e}")
    finally:
        input("Нажмите Enter для выхода...")


if __name__ == '__main__':
    main()
