# Version 1.0 - 22.06.2026 14:25:00 GMT
# Утилита для анализа ZIP-архивов в выбранной директории и её поддиректориях.
# Распределяет архивы по категориям: повреждённые, только изображения, только PDF, PDF+другие.
# Требует только stdlib + tkinter.

import os
import zipfile
import time
from tkinter import Tk, filedialog

# --- Функции ---

def choose_directory():
    """Открывает диалог выбора директории и возвращает путь."""
    root = Tk()
    root.withdraw()
    directory = filedialog.askdirectory(title="Выберите папку для проверки ZIP-файлов")
    root.destroy()
    return directory

def is_image(filename):
    """Проверяет, является ли файл изображением по расширению."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}

def analyze_zip(zip_path):
    """Проверяет zip-файл, возвращает (status, extensions)."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            bad_file = zf.testzip()
            if bad_file:
                return ('broken', None)
            names = zf.namelist()
            exts = {os.path.splitext(n)[1].lower() for n in names if not n.endswith('/')}
            return ('ok', exts)
    except Exception:
        return ('broken', None)

def print_progress(done, total, start_time):
    """Выводит прогресс обработки в одну строку."""
    elapsed = time.time() - start_time
    rate = done / elapsed if elapsed > 0 else 0
    eta = (total - done) / rate if rate > 0 else 0
    print(f"\rОбработано {done}/{total} ({rate:.1f} арх/с, ост. ~{eta:.0f} с) ", end='', flush=True)

# --- Основная логика ---

def main():
    try:
        directory = choose_directory()
        if not directory or not os.path.isdir(directory):
            print("Директория не выбрана или не существует.")
            input("\nНажмите Enter для выхода...")
            return

        zip_files = []
        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith('.zip'):
                    zip_files.append(os.path.join(root, file))

        total = len(zip_files)
        print(f"\nНайдено {total} zip-файлов. Начинаю проверку...\n")

        broken = []
        only_images = []
        only_pdf = []
        pdf_and_other = []

        start = time.time()
        for i, zip_path in enumerate(zip_files, 1):
            name = os.path.basename(zip_path)
            status, exts = analyze_zip(zip_path)
            if status == 'broken':
                broken.append(name)
            elif exts:
                if all(is_image("a" + ext) for ext in exts):
                    only_images.append(name)
                elif exts == {'.pdf'}:
                    only_pdf.append(name)
                elif '.pdf' in exts and len(exts) > 1:
                    pdf_and_other.append(name)
            print_progress(i, total, start)

        elapsed = time.time() - start
        print()  # перенос после прогресса

        def show(label, items):
            if items:
                print(f"{label} ({len(items)}): {'; '.join(items)}")
            else:
                print(f"{label} (0): нет")

        print("\nРезультаты анализа:")
        show("Нарушена целостность zip", broken)
        show("Содержит только изображения", only_images)
        show("Содержит только pdf", only_pdf)
        show("Содержит pdf и другие файлы", pdf_and_other)
        print(f"\nВсего проанализировано {total} zip")
        print(f"Проверка завершена за {elapsed:.1f} сек.")

    except Exception as err:
        print(f"\n[ОШИБКА]: {err}\n")

    input("\nНажмите Enter для выхода...")

if __name__ == "__main__":
    main()
