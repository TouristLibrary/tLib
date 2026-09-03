# Version 1.0 - 22.06.2026 14:25:00 GMT
# Утилита для анализа размеров PDF-файлов в директории и ZIP-архивах.
# Выводит гистограмму распределения по размерам (карман 5 МБ), медиану и стандартное отклонение.
# Требует только stdlib + tkinter.

import os
import sys
import zipfile
import statistics
from tkinter import Tk, filedialog
from collections import defaultdict

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

MB = 1024 * 1024
BUCKET_SIZE = 5 * MB

# --- Функции ---

def choose_directory():
    """Открывает диалог выбора директории и возвращает путь."""
    root = Tk()
    root.withdraw()
    directory = filedialog.askdirectory(title="Выберите папку для анализа PDF файлов")
    root.destroy()
    return directory

def format_size(bytes_size):
    """Форматирует размер в человекочитаемый вид."""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < MB:
        return f"{bytes_size / 1024:.2f} KB"
    elif bytes_size < 1024 * MB:
        return f"{bytes_size / MB:.2f} MB"
    else:
        return f"{bytes_size / (1024 * MB):.2f} GB"

def collect_pdf_sizes(directory):
    """Собирает размеры всех PDF-файлов в директории и внутри ZIP-архивов."""
    pdf_sizes = []

    all_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            all_files.append(os.path.join(root, file))

    total = len(all_files)
    print(f"\nНайдено {total} файлов. Анализирую...\n")

    for i, file_path in enumerate(all_files, 1):
        file_lower = file_path.lower()

        if file_lower.endswith('.pdf'):
            try:
                pdf_sizes.append(os.path.getsize(file_path))
            except Exception:
                pass

        elif file_lower.endswith('.zip'):
            try:
                with zipfile.ZipFile(file_path, 'r') as zf:
                    for info in zf.infolist():
                        if not info.is_dir() and info.filename.lower().endswith('.pdf'):
                            pdf_sizes.append(info.file_size)
            except Exception:
                pass

        if i % 500 == 0 or i == total:
            print(f"\r  {i}/{total} файлов обработано", end='', flush=True)

    print()
    return pdf_sizes

def build_histogram(sizes):
    """Строит гистограмму распределения по карманам."""
    if not sizes:
        return {}
    histogram = defaultdict(int)
    for size in sizes:
        bucket = (size // BUCKET_SIZE) * BUCKET_SIZE
        histogram[bucket] += 1
    return dict(sorted(histogram.items()))

def print_histogram(histogram, total_files):
    """Выводит гистограмму."""
    print("\n" + "=" * 70)
    print("ГИСТОГРАММА РАСПРЕДЕЛЕНИЯ ПО РАЗМЕРАМ (карман 5 МБ)")
    print("=" * 70)
    if not histogram:
        print("Нет данных для отображения")
        return
    max_count = max(histogram.values())
    max_bar_width = 50
    print(f"{'Размер (МБ)':<20s} {'Файлов':<10s} {'Процент':<10s} {'График'}")
    print("-" * 70)
    for bucket, count in histogram.items():
        start_mb = bucket / MB
        end_mb = (bucket + BUCKET_SIZE) / MB
        percent = (count / total_files) * 100
        bar = "█" * int((count / max_count) * max_bar_width)
        range_str = f"{start_mb:.0f}-{end_mb:.0f}"
        print(f"{range_str:<20s} {count:<10d} {percent:>6.1f}%   {bar}")

def print_statistics(sizes):
    """Выводит статистические показатели."""
    if not sizes:
        print("\nНет данных для статистики")
        return
    print("\n" + "=" * 70)
    print("СТАТИСТИКА")
    print("=" * 70)
    print(f"Всего PDF файлов:      {len(sizes)}")
    print(f"Общий размер:          {format_size(sum(sizes))}")
    print(f"Минимальный размер:    {format_size(min(sizes))}")
    print(f"Максимальный размер:   {format_size(max(sizes))}")
    print(f"Средний размер:        {format_size(int(statistics.mean(sizes)))}")
    print(f"Медианный размер:      {format_size(int(statistics.median(sizes)))}")
    if len(sizes) >= 2:
        print(f"Станд. отклонение:     {format_size(int(statistics.stdev(sizes)))}")
    else:
        print("Станд. отклонение:     N/A (требуется >=2 файлов)")

# --- Основная логика ---

def main():
    try:
        print("=" * 70)
        print("  Анализ размеров PDF файлов")
        print("=" * 70)
        directory = choose_directory()
        if not directory or not os.path.isdir(directory):
            print("\nДиректория не выбрана или не существует.")
            input("\nНажмите Enter для выхода...")
            return
        print(f"\nВыбрана директория: {directory}")
        pdf_sizes = collect_pdf_sizes(directory)
        if not pdf_sizes:
            print("\nPDF файлы не найдены в указанной директории.")
            input("\nНажмите Enter для выхода...")
            return
        histogram = build_histogram(pdf_sizes)
        print_histogram(histogram, len(pdf_sizes))
        print_statistics(pdf_sizes)
        print("\n" + "=" * 70)
        print("Анализ завершен!")
    except Exception as err:
        print(f"\n[ОШИБКА]: {err}\n")

    input("\nНажмите Enter для выхода...")

if __name__ == "__main__":
    main()
