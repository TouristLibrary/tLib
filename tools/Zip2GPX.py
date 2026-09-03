# Version 1.0 - 22.06.2026 14:25:00 GMT
# Утилита для извлечения GPS/гео-файлов из ZIP-архивов.
# Рекурсивно обходит директорию, извлекает файлы геоданных (.gpx, .kml, .kmz, .plt, .geojson)
# и упаковывает их в ZIP-архивы с теми же именами в папку GPX (рядом со скриптом).
# Ведёт log.txt с расширениями по частоте. Требует только stdlib + tkinter.

import os
import zipfile
from collections import Counter
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
from tkinter import ttk

# Поддерживаемые гео/GPS форматы (расширения с ведущей точкой)
GEO_EXTS = [".gpx", ".kml", ".kmz", ".plt", ".geojson"]


def ask_directory():
    """Показывает диалог выбора директории."""
    root = tk.Tk()
    root.withdraw()
    dir_path = filedialog.askdirectory(title="Выберите папку для поиска zip-архивов")
    root.destroy()
    return dir_path


def list_zip_files(directory):
    """Рекурсивно возвращает пути ко всем ZIP-файлам в директории."""
    for root, _, files in os.walk(directory):
        for fname in files:
            if fname.lower().endswith('.zip'):
                yield os.path.join(root, fname)


def extract_geo_files_from_zip(zip_path, geo_exts, target_dir):
    """Извлекает гео-файлы из ZIP и упаковывает их в новый архив в target_dir."""
    extracted_files = []
    ext_counter = Counter()
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                ext = os.path.splitext(name)[1].lower()
                if ext in geo_exts:
                    extracted_files.append((name, zf.read(name)))
                    ext_counter[ext] += 1
    except Exception as e:
        print(f"Ошибка при работе с архивом {zip_path}: {e}")
        return [], Counter()
    if extracted_files:
        out_zip_name = Path(zip_path).stem + '.zip'
        out_zip_path = os.path.join(target_dir, out_zip_name)
        try:
            with zipfile.ZipFile(out_zip_path, 'w', zipfile.ZIP_DEFLATED) as out_zip:
                for fname, content in extracted_files:
                    out_zip.writestr(os.path.basename(fname), content)
        except Exception as e:
            print(f"Ошибка при создании архива {out_zip_path}: {e}")
            return [], Counter()
        return [out_zip_name], ext_counter
    return [], Counter()


def show_progressbar(task_gen, total, title="Обработка файлов"):
    """Показывает Tkinter-прогресс и по шагам прокручивает генератор задач."""
    window = tk.Tk()
    window.title(title)
    window.geometry("400x100")
    pb = ttk.Progressbar(window, orient='horizontal', length=350, mode='determinate', maximum=total)
    pb.pack(pady=20)
    status = tk.Label(window, text="Старт...")
    status.pack()
    window.update()
    count = 0
    for val in task_gen:
        count += 1
        pb['value'] = count
        status['text'] = f"{count} / {total}"
        window.update()
        yield val
    status['text'] = "Готово!"
    window.update()
    window.after(1000, window.destroy)
    window.mainloop()


def main():
    try:
        directory = ask_directory()
        if not directory:
            print("Папка не выбрана. Выход.")
            input("Нажмите Enter для выхода...")
            return

        zip_files = list(list_zip_files(directory))
        total = len(zip_files)
        if not zip_files:
            print("В выбранной директории zip-архивы не найдены.")
            input("Нажмите Enter для выхода...")
            return

        gpx_dir = os.path.join(os.getcwd(), "GPX")
        os.makedirs(gpx_dir, exist_ok=True)

        global_ext_counter = Counter()
        file_log = []

        def process_zip_files():
            for zip_path in zip_files:
                out_files, ext_counter = extract_geo_files_from_zip(zip_path, GEO_EXTS, gpx_dir)
                for ext, cnt in ext_counter.items():
                    global_ext_counter[ext] += cnt
                if out_files:
                    file_log.append((out_files[0], dict(ext_counter)))
                yield

        for _ in show_progressbar(process_zip_files(), total):
            pass

        log_path = os.path.join(gpx_dir, "log.txt")
        with open(log_path, "w", encoding="utf-8") as log:
            ext_str = ", ".join(f"{ext}:{count}" for ext, count in global_ext_counter.most_common())
            log.write(ext_str + "\n")
            for zip_out_name, ext_map in file_log:
                ext_part = ", ".join(
                    f"{ext}:{count}"
                    for ext, count in sorted(ext_map.items(), key=lambda x: -x[1])
                )
                log.write(f"{zip_out_name} [{ext_part}]\n")

        print(f"Готово! Обработка завершена.\nРезультаты и log.txt — в папке: {gpx_dir}")

    except Exception as e:
        print(f"Произошла ошибка: {e}")
    finally:
        input("Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()
