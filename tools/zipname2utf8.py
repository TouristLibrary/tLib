# Version 1.0 - 22.06.2026 14:25:00 GMT
# Утилита для нормализации кодировок имён файлов в ZIP-архивах.
# Перебирает zip-файлы в выбранной папке, парсит бинарно локальные заголовки для получения
# "сырых" байтов имён файлов, определяет кодировку, перепаковывает с UTF-8-именами,
# складывает в "<папка>.utf8", логирует результат. Требует только stdlib + tkinter.

import os
import sys
import struct
import shutil
import tempfile
import tkinter as tk
from tkinter import filedialog
import zipfile

# Настройка консоли на UTF-8 для вывода кириллицы
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Порядок кодировок синхронизирован с ZIP_ENCODINGS в config/media.py:
# ['utf-8', 'cp866', 'cp1251', 'latin1']
# CP866 (DOS) проверяется перед CP1251, так как многие старые архивы созданы в DOS/Windows 9x.
ZIP_ENCODINGS = ('utf-8', 'cp866', 'cp1251', 'latin1')


def is_cp437_only(raw_name_bytes):
    """Проверяет, содержит ли имя только ASCII-символы (не требует перекодирования)."""
    try:
        decoded = raw_name_bytes.decode('cp437')
        for c in decoded:
            code = ord(c)
            if not (32 <= code <= 126):
                return False
        return True
    except Exception:
        return False


def is_valid_name(s):
    """Проверяет, что строка содержит только разрешённые символы (ASCII + кириллица)."""
    for c in s:
        code = ord(c)
        if code in (9, 10, 13):
            continue
        if 32 <= code <= 126:
            continue
        if 0x0400 <= code <= 0x04FF:
            continue
        if 0x0500 <= code <= 0x052F:
            continue
        if code in (0x401, 0x451):
            continue
        return False
    return True


def detect_encoding(raw_bytes):
    """Определяет кодировку сырых байтов имени файла из ZIP."""
    for enc in ZIP_ENCODINGS:
        try:
            decoded = raw_bytes.decode(enc)
            if is_valid_name(decoded):
                return enc
        except Exception:
            continue
    return 'cp1251'


def extract_raw_names(zip_path):
    """Возвращает список (raw_bytes_name, ZipInfo), считывая локальные заголовки бинарно."""
    result = []
    with open(zip_path, 'rb') as f, zipfile.ZipFile(zip_path, 'r') as zf:
        offset_map = {zi.header_offset: zi for zi in zf.infolist()}
        while True:
            header = f.read(4)
            if len(header) < 4:
                break
            if header != b'PK\x03\x04':
                f.seek(-3, 1)
                continue
            local_header = f.read(26)
            if len(local_header) < 26:
                break
            fields = struct.unpack('<HHHHHIIIHH', local_header)
            fname_len = fields[8]
            extra_len = fields[9]
            raw_fname = f.read(fname_len)
            f.read(extra_len)
            curr_offset = f.tell() - (30 + fname_len + extra_len)
            if curr_offset in offset_map:
                result.append((raw_fname, offset_map[curr_offset]))
                f.seek(offset_map[curr_offset].compress_size, 1)
    return result


def repack_zip_raw(input_zip, output_zip, log_file):
    """Перепаковывает ZIP с UTF-8-именами файлов. Возвращает True, если перепаковка была нужна."""
    tmpdir = tempfile.mkdtemp()
    repacked = False
    try:
        raw_names_and_infos = extract_raw_names(input_zip)
        need_repack = False
        decoded_map = {}
        for raw_fname, zi in raw_names_and_infos:
            if not is_cp437_only(raw_fname):
                need_repack = True
            enc = detect_encoding(raw_fname)
            try:
                decoded_name = raw_fname.decode(enc)
            except Exception:
                decoded_name = zi.filename
            decoded_map[zi.filename] = decoded_name

        with zipfile.ZipFile(input_zip, 'r') as zin:
            for zi in zin.infolist():
                zin.extract(zi, tmpdir)

        if not need_repack:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return False

        with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for orig_name, decoded_name in decoded_map.items():
                path = os.path.join(tmpdir, orig_name)
                zout.write(path, decoded_name)

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"Repacked: {os.path.basename(input_zip)} -> {os.path.basename(output_zip)}\n")
        print(f"Перепакован: {os.path.basename(input_zip)} -> {os.path.basename(output_zip)}")
        repacked = True
    except Exception as err:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"ERROR in {os.path.basename(input_zip)}: {err}\n")
        print(f"Ошибка в {os.path.basename(input_zip)}: {err}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return repacked


def main():
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title='Выберите папку с zip-архивами')
    if not folder:
        print("Папка не выбрана. Завершение.")
        input('Нажмите Enter для выхода...')
        return

    out_folder = folder + ".utf8"
    os.makedirs(out_folder, exist_ok=True)
    log_file = os.path.join(out_folder, "log.txt")
    files = [f for f in os.listdir(folder) if f.lower().endswith('.zip')]
    if not files:
        print("В папке нет zip-файлов.")
        input('Нажмите Enter для выхода...')
        return

    any_repacked = False
    for fname in files:
        res = repack_zip_raw(
            os.path.join(folder, fname),
            os.path.join(out_folder, fname),
            log_file,
        )
        if res:
            any_repacked = True

    if any_repacked:
        print(f"Завершено. Перепакованные архивы и log.txt — в {out_folder}")
    else:
        print("Все архивы содержат только латинские имена файлов. Перепаковка не требовалась.")
    input('Нажмите Enter для выхода...')


if __name__ == '__main__':
    main()
