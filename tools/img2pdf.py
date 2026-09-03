# Version 1.0 - 22.06.2026 14:25:00 GMT
# Утилита для конвертации изображений (или папки с изображениями) в единый PDF-файл.
# Поддерживает JPG, PNG, BMP, TIFF, WEBP, GIF (любой регистр расширения).
# Требует Pillow: pip install Pillow (не входит в основной requirements.txt).

import os
import sys
import traceback

try:
    from PIL import Image
except ImportError:
    print(
        "ОШИБКА: библиотека Pillow не установлена.\n"
        "Установите её командой:  pip install Pillow"
    )
    input("Нажмите Enter для выхода...")
    sys.exit(1)


def log_error(error_message):
    """Логирует ошибку в файл error.log."""
    with open('error.log', 'a', encoding='utf-8') as log:
        log.write(f"{error_message}\n{'-' * 60}\n")


def get_image_files(input_path):
    """
    Возвращает список путей к изображениям из папки или одного файла.
    Выводит найденные файлы для диагностики.
    """
    supported_ext = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp', '.gif')
    image_files = []
    if os.path.isdir(input_path):
        for file in sorted(os.listdir(input_path)):
            file_path = os.path.join(input_path, file)
            if os.path.isfile(file_path) and file.lower().endswith(supported_ext):
                image_files.append(file_path)
    elif os.path.isfile(input_path) and input_path.lower().endswith(supported_ext):
        image_files.append(input_path)
    else:
        raise ValueError("Указан некорректный путь или неподдерживаемый формат файла.")
    if not image_files:
        raise ValueError("В указанной папке нет изображений поддерживаемых форматов.")
    print(f"Найдено {len(image_files)} изображений:")
    for f in image_files:
        print(f" - {f}")
    return image_files


def convert_images_to_pdf(image_paths, output_pdf_path):
    """Преобразует список изображений в единый PDF-файл (все страницы в RGB)."""
    images = []
    for path in image_paths:
        try:
            with Image.open(path) as img:
                images.append(img.convert('RGB').copy())
        except Exception as e:
            print(f"Не удалось обработать файл: {path}\nОшибка: {e}")
    if not images:
        raise RuntimeError("Не удалось загрузить ни одно изображение для создания PDF.")
    images[0].save(output_pdf_path, save_all=True, append_images=images[1:])
    print(f"PDF успешно сохранён: {output_pdf_path}")


def main():
    try:
        print("=== Конвертер изображений в PDF ===")
        input_path = input("Введите путь к файлу-изображению или папке с изображениями: ").strip()
        output_pdf = input("Введите желаемое имя выходного PDF-файла (например, result.pdf): ").strip()

        image_files = get_image_files(input_path)
        convert_images_to_pdf(image_files, output_pdf)

    except Exception as e:
        error_msg = f"Произошла ошибка:\n{traceback.format_exc()}"
        print(error_msg)
        log_error(error_msg)

    finally:
        input("Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()
