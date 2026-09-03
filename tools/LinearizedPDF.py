#!/usr/bin/env python3
# Version 1.0 - 22.06.2026 14:25:00 GMT
# Утилита для проверки линеаризации PDF (Fast Web View) и её выполнения.
# Поддерживает: QPDF, Ghostscript, pikepdf, PDFtk.
# Опциональные зависимости: PyPDF2 (pip install PyPDF2), pikepdf (pip install pikepdf).
# Обязательных внешних зависимостей нет — при отсутствии инструментов выводит подсказку.

import sys
import subprocess
import shutil
import re
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# Опциональные зависимости
try:
    from PyPDF2 import PdfReader
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

try:
    import pikepdf
    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False

try:
    from tkinter import Tk, filedialog
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False


# ============================================================================
# УТИЛИТЫ ДЛЯ РАБОТЫ С ФАЙЛАМИ
# ============================================================================

def select_file_gui() -> Optional[Path]:
    """Открывает GUI диалог для выбора PDF файла."""
    if not HAS_TKINTER:
        return None
    try:
        root = Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        file_path = filedialog.askopenfilename(
            title="Выберите PDF файл",
            filetypes=[("PDF файлы", "*.pdf"), ("Все файлы", "*.*")],
            initialdir=Path.cwd()
        )
        root.destroy()
        return Path(file_path) if file_path else None
    except Exception as e:
        print(f"Ошибка GUI диалога: {e}")
        return None


def select_file_interactive(action: str = "обработки") -> Optional[Path]:
    """Интерактивный выбор файла через консоль."""
    print(f"\nВыбор PDF файла для {action}")
    print("=" * 70)
    cwd = Path.cwd()
    print(f"Текущая директория: {cwd}\n")
    pdf_files = sorted(cwd.glob("*.pdf"))
    if pdf_files:
        print("PDF файлы в текущей директории:")
        for i, pdf_file in enumerate(pdf_files, 1):
            size_mb = pdf_file.stat().st_size / (1024 * 1024)
            print(f"  [{i}] {pdf_file.name:<40s} {size_mb:>8.2f} MB")
        print()
    print("Варианты:")
    if pdf_files:
        print("  - Введите номер файла из списка")
    print("  - Введите полный или относительный путь к файлу")
    if HAS_TKINTER:
        print("  - Нажмите Enter для GUI диалога выбора")
    print("  - Введите 'q' для выхода")
    print()
    choice = input("Ваш выбор: ").strip()
    if choice.lower() == 'q':
        return None
    if not choice and HAS_TKINTER:
        return select_file_gui()
    if choice.isdigit() and pdf_files:
        index = int(choice) - 1
        if 0 <= index < len(pdf_files):
            return pdf_files[index]
        else:
            print(f"Неверный номер. Должен быть от 1 до {len(pdf_files)}")
            return None
    pdf_path = Path(choice)
    if not pdf_path.exists():
        pdf_path = cwd / choice
        if not pdf_path.exists():
            print(f"Файл не найден: {choice}")
            return None
    return pdf_path


def format_size(bytes_size: int) -> str:
    """Форматирует размер файла в человекочитаемый вид."""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.2f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"


# ============================================================================
# ПРОВЕРКА ЛИНЕАРИЗАЦИИ
# ============================================================================

def check_linearization_structure(pdf_path: Path) -> Tuple[bool, str, Dict[str, bool]]:
    """
    Детальная проверка линеаризации через разбор структуры PDF.
    Возвращает: (is_linearized, details, params_dict)
    """
    try:
        with open(pdf_path, 'rb') as f:
            header = f.read(4096).decode('latin-1', errors='ignore')
            if not header.startswith('%PDF-'):
                return False, "Не PDF файл", {}
            if '/Linearized' not in header:
                return False, "Нет маркера /Linearized", {}
            first_obj_match = re.search(r'1\s+0\s+obj\s*<<(.*?)>>', header, re.DOTALL)
            if not first_obj_match:
                return False, "/Linearized найден, но не в объекте 1", {}
            first_obj_content = first_obj_match.group(1)
            params = {
                'Linearized': '/Linearized' in first_obj_content,
                'L': '/L' in first_obj_content,
                'H': '/H' in first_obj_content,
                'O': '/O' in first_obj_content,
                'E': '/E' in first_obj_content,
                'N': '/N' in first_obj_content,
                'T': '/T' in first_obj_content,
            }
            if not params['Linearized']:
                return False, "/Linearized не в первом объекте", params
            required_count = sum([params['L'], params['O'], params['E'], params['N']])
            if required_count >= 4:
                return True, "Полная линеаризация", params
            elif params['Linearized']:
                return True, f"Частичная ({required_count}/4 параметров)", params
            else:
                return False, "Неполная структура", params
    except Exception as e:
        return False, f"Ошибка: {e}", {}


def check_linearization_qpdf(pdf_path: Path) -> Tuple[Optional[bool], str]:
    """
    Проверка линеаризации через QPDF --check-linearization.
    Самый надёжный метод. Возвращает: (is_linearized, message)
    """
    if not shutil.which('qpdf'):
        return None, "QPDF не установлен"
    try:
        result = subprocess.run(
            ['qpdf', '--check-linearization', str(pdf_path)],
            capture_output=True, text=True, timeout=30
        )
        output = (result.stdout + result.stderr).lower()
        if 'is linearized' in output or (result.returncode == 0 and 'not linearized' not in output):
            return True, "Файл линеаризован (QPDF)"
        elif 'is not linearized' in output or 'not linearized' in output:
            return False, "Файл НЕ линеаризован (QPDF)"
        else:
            return None, f"Неопределённый статус (код {result.returncode})"
    except subprocess.TimeoutExpired:
        return None, "Timeout проверки"
    except Exception as e:
        return None, f"Ошибка: {e}"


def get_pdf_info_pypdf2(pdf_path: Path) -> Dict[str, Any]:
    """Получает дополнительную информацию через PyPDF2 (опционально)."""
    if not HAS_PYPDF2:
        return {}
    try:
        reader = PdfReader(pdf_path)
        info: Dict[str, Any] = {
            'pages': len(reader.pages),
            'encrypted': reader.is_encrypted,
        }
        if reader.metadata:
            info['metadata'] = {
                str(k).replace('/', ''): str(v) if v else ''
                for k, v in reader.metadata.items()
            }
        return info
    except Exception as e:
        return {'error': str(e)}


def analyze_pdf(pdf_path: Path, verbose: bool = True) -> Dict[str, Any]:
    """Полный анализ PDF файла. Возвращает словарь с результатами всех проверок."""
    if not pdf_path.exists():
        return {'error': 'Файл не найден'}
    results: Dict[str, Any] = {
        'path': pdf_path,
        'name': pdf_path.name,
        'size': pdf_path.stat().st_size,
    }
    is_lin_struct, details_struct, params = check_linearization_structure(pdf_path)
    results['structure_check'] = {
        'linearized': is_lin_struct, 'details': details_struct, 'params': params
    }
    is_lin_qpdf, msg_qpdf = check_linearization_qpdf(pdf_path)
    results['qpdf_check'] = {'linearized': is_lin_qpdf, 'message': msg_qpdf}
    results['pypdf2'] = get_pdf_info_pypdf2(pdf_path)
    if is_lin_qpdf is not None:
        results['is_linearized'] = is_lin_qpdf
        results['confidence'] = 'high'
    else:
        results['is_linearized'] = is_lin_struct
        results['confidence'] = 'medium'
    return results


def print_pdf_analysis(results: Dict[str, Any]):
    """Выводит результаты анализа PDF."""
    print(f"\nАнализ PDF: {results['name']}")
    print("=" * 70)
    print(f"Путь:   {results['path']}")
    print(f"Размер: {format_size(results['size'])}")
    pypdf2 = results.get('pypdf2', {})
    if 'pages' in pypdf2:
        print(f"Страниц: {pypdf2['pages']}")
    if pypdf2.get('encrypted'):
        print("Зашифрован: Да")
    print()
    struct = results['structure_check']
    print(f"Проверка структуры PDF: {struct['details']}")
    if struct['params']:
        params_str = ", ".join(
            f"{k}:{'да' if v else 'нет'}" for k, v in struct['params'].items()
        )
        print(f"  Параметры: {params_str}")
    print()
    qpdf = results['qpdf_check']
    print(f"Проверка QPDF (эталонная): {qpdf['message']}")
    print()
    print("ИТОГОВОЕ ЗАКЛЮЧЕНИЕ:")
    if results['is_linearized']:
        print("  Файл ЛИНЕАРИЗОВАН (Fast Web View)")
        print("  Файл будет загружаться постепенно в браузере")
    else:
        print("  Файл НЕ ЛИНЕАРИЗОВАН")
        print("  Рекомендуется линеаризация для веб-просмотра")
    if results['confidence'] == 'medium':
        print("  Установите QPDF для более точной проверки")
    print()


# ============================================================================
# ЛИНЕАРИЗАЦИЯ
# ============================================================================

def check_tool_available(tool_name: str) -> bool:
    """Проверяет доступность внешней утилиты в PATH."""
    return shutil.which(tool_name) is not None


def get_available_linearization_methods() -> Dict[str, Dict[str, Any]]:
    """Возвращает доступные методы линеаризации с приоритетами."""
    methods: Dict[str, Dict[str, Any]] = {}
    if check_tool_available('qpdf'):
        methods['qpdf'] = {
            'name': 'QPDF', 'description': 'Самый надёжный метод (рекомендуется)',
            'quality': '*****', 'priority': 1
        }
    gs_cmd = 'gswin64c' if check_tool_available('gswin64c') else ('gs' if check_tool_available('gs') else None)
    if gs_cmd:
        methods['gs'] = {
            'name': 'Ghostscript', 'description': 'Хорошая совместимость',
            'quality': '****', 'priority': 2, 'command': gs_cmd
        }
    if HAS_PIKEPDF:
        methods['pikepdf'] = {
            'name': 'pikepdf', 'description': 'Python библиотека (быстро)',
            'quality': '****', 'priority': 3
        }
    if check_tool_available('pdftk'):
        methods['pdftk'] = {
            'name': 'PDFtk', 'description': 'Классический инструмент',
            'quality': '***', 'priority': 4
        }
    return methods


def linearize_with_qpdf(input_path: Path, output_path: Path) -> Tuple[bool, str]:
    """Линеаризация через QPDF."""
    try:
        result = subprocess.run(
            ['qpdf', '--linearize', str(input_path), str(output_path)],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0 and output_path.exists():
            return True, "Успешно"
        return False, f"QPDF: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "Превышено время ожидания (5 минут)"
    except Exception as e:
        return False, str(e)


def linearize_with_ghostscript(input_path: Path, output_path: Path) -> Tuple[bool, str]:
    """Линеаризация через Ghostscript."""
    try:
        gs_cmd = 'gswin64c' if check_tool_available('gswin64c') else 'gs'
        result = subprocess.run(
            [gs_cmd, '-dBATCH', '-dNOPAUSE', '-sDEVICE=pdfwrite',
             '-dFastWebView=true', f'-sOutputFile={output_path}', str(input_path)],
            capture_output=True, text=True, timeout=300
        )
        if output_path.exists() and output_path.stat().st_size > 0:
            return True, "Успешно"
        return False, f"Ghostscript: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "Превышено время ожидания (5 минут)"
    except Exception as e:
        return False, str(e)


def linearize_with_pikepdf(input_path: Path, output_path: Path) -> Tuple[bool, str]:
    """Линеаризация через pikepdf."""
    try:
        with pikepdf.open(input_path) as pdf:
            pdf.save(output_path, linearize=True)
        return True, "Успешно"
    except Exception as e:
        return False, str(e)


def linearize_with_pdftk(input_path: Path, output_path: Path) -> Tuple[bool, str]:
    """Линеаризация через PDFtk."""
    try:
        result = subprocess.run(
            ['pdftk', str(input_path), 'output', str(output_path), 'linearize'],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0 and output_path.exists():
            return True, "Успешно"
        return False, f"PDFtk: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "Превышено время ожидания (5 минут)"
    except Exception as e:
        return False, str(e)


def linearize_pdf(input_path: Path, output_path: Path, method: str) -> Tuple[bool, str]:
    """Универсальный диспетчер линеаризации."""
    dispatch = {
        'qpdf': linearize_with_qpdf,
        'gs': linearize_with_ghostscript,
        'pikepdf': linearize_with_pikepdf,
        'pdftk': linearize_with_pdftk,
    }
    fn = dispatch.get(method)
    if fn:
        return fn(input_path, output_path)
    return False, f"Неизвестный метод: {method}"


def choose_linearization_method(methods: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Интерактивный выбор метода линеаризации."""
    if not methods:
        return None
    method_list = sorted(methods.items(), key=lambda x: x[1]['priority'])
    print("\nДоступные методы линеаризации:")
    print("=" * 70)
    for i, (key, info) in enumerate(method_list, 1):
        print(f"  [{i}] {info['name']:20s} {info['quality']}")
        print(f"      {info['description']}")
        print()
    if len(method_list) == 1:
        print(f"Используется единственный доступный метод: {method_list[0][1]['name']}")
        return method_list[0][0]
    print("Варианты:")
    print("  - Введите номер метода")
    print("  - Нажмите Enter для рекомендованного")
    print()
    choice = input("Ваш выбор: ").strip()
    if not choice:
        return method_list[0][0]
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(method_list):
            return method_list[index][0]
    return None


# ============================================================================
# ГЛАВНЫЕ ФУНКЦИИ
# ============================================================================

def check_mode():
    """Режим проверки линеаризации PDF."""
    print("=" * 70)
    print("  PDF Checker - Проверка линеаризации PDF")
    print("=" * 70)
    if len(sys.argv) >= 2:
        pdf_path = Path(sys.argv[1])
        if not pdf_path.exists():
            print(f"\nФайл не найден: {pdf_path}")
            sys.exit(1)
    else:
        pdf_path = select_file_interactive("проверки")
        if not pdf_path:
            print("\nВыход из программы")
            sys.exit(0)
    results = analyze_pdf(pdf_path)
    print_pdf_analysis(results)
    if not results['is_linearized']:
        print("=" * 70)
        choice = input("Линеаризовать этот файл? (y/n): ").strip().lower()
        if choice == 'y':
            linearize_mode(pdf_path)
            return
    if len(sys.argv) < 2:
        print("=" * 70)
        choice = input("Проверить другой файл? (y/n): ").strip().lower()
        if choice == 'y':
            print()
            check_mode()


def linearize_mode(input_path: Optional[Path] = None):
    """Режим линеаризации PDF."""
    if input_path is None:
        print("=" * 70)
        print("  PDF Linearizer - Оптимизация для Fast Web View")
        print("=" * 70)
        if len(sys.argv) >= 2:
            input_path = Path(sys.argv[1])
            if not input_path.exists():
                print(f"\nФайл не найден: {input_path}")
                sys.exit(1)
        else:
            input_path = select_file_interactive("линеаризации")
            if not input_path:
                print("\nВыход из программы")
                sys.exit(0)
    print(f"\nФайл: {input_path.name}")
    print("=" * 70)
    results = analyze_pdf(input_path, verbose=False)
    print(f"Размер: {format_size(results['size'])}")
    print(f"Линеаризация: {'Уже линеаризован' if results['is_linearized'] else 'Требуется'}")
    if results['is_linearized']:
        choice = input("\nФайл уже линеаризован. Продолжить? (y/n): ").strip().lower()
        if choice != 'y':
            return
    methods = get_available_linearization_methods()
    if not methods:
        print("\nНе найдено ни одного инструмента для линеаризации!")
        print("\nУстановите один из:")
        print("  QPDF (рекомендуется): choco install qpdf")
        print("    или https://github.com/qpdf/qpdf/releases")
        print("  Ghostscript: https://www.ghostscript.com/")
        print("  pikepdf: pip install pikepdf")
        sys.exit(1)
    method = choose_linearization_method(methods)
    if not method:
        print("\nМетод не выбран")
        sys.exit(1)
    output_path = input_path.parent / f"{input_path.stem}_linearized.pdf"
    print(f"\nВыходной файл: {output_path.name}")
    if output_path.exists():
        choice = input(f"\nФайл {output_path.name} существует. Перезаписать? (y/n): ").strip().lower()
        if choice != 'y':
            return
    print(f"\nЛинеаризация ({methods[method]['name']})...")
    print("=" * 70)
    start_time = time.time()
    success, message = linearize_pdf(input_path, output_path, method)
    elapsed_time = time.time() - start_time
    if success:
        print(f"Завершено за {elapsed_time:.1f} сек")
        print("\nСравнение:")
        print("-" * 70)
        output_size = output_path.stat().st_size
        size_diff = output_size - results['size']
        percent = (size_diff / results['size']) * 100
        print(f"{'':20s} {'ДО':>15s} {'ПОСЛЕ':>15s} {'ИЗМЕНЕНИЕ':>15s}")
        print(f"{'Размер':20s} {format_size(results['size']):>15s} {format_size(output_size):>15s} ", end='')
        if size_diff > 0:
            print(f"+{format_size(size_diff)} (+{percent:.1f}%)")
        elif size_diff < 0:
            print(f"{format_size(abs(size_diff))} ({percent:.1f}%)")
        else:
            print("без изменений")
        print("\nПроверка результата...")
        result_analysis = analyze_pdf(output_path, verbose=False)
        print(f"{'Линеаризация':20s} ", end='')
        print(f"{'Да' if results['is_linearized'] else 'Нет':>15s} ", end='')
        print(f"{'Да' if result_analysis['is_linearized'] else 'Нет':>15s}")
        if result_analysis['is_linearized']:
            print("\nУСПЕХ! Файл линеаризован для Fast Web View")
            print("Теперь PDF будет загружаться постепенно в браузере")
        else:
            print("\nПредупреждение: линеаризация не подтверждена")
            print("Попробуйте другой метод или проверьте исходный файл")
        print(f"\nСохранено: {output_path}")
    else:
        print(f"Ошибка: {message}")
        if output_path.exists():
            output_path.unlink()
        sys.exit(1)
    if len(sys.argv) < 2:
        print("\n" + "=" * 70)
        choice = input("Обработать другой файл? (y/n): ").strip().lower()
        if choice == 'y':
            print()
            linearize_mode()


def interactive_menu():
    """Интерактивное меню выбора режима."""
    print("=" * 70)
    print("  PDF Linearizer & Checker v2.0")
    print("=" * 70)
    print("\nВыберите режим работы:")
    print("  [1] Проверить линеаризацию PDF")
    print("  [2] Линеаризовать PDF")
    print("  [q] Выход")
    print()
    choice = input("Ваш выбор: ").strip().lower()
    if choice == '1':
        check_mode()
    elif choice == '2':
        linearize_mode()
    elif choice == 'q':
        print("\nВыход из программы")
        sys.exit(0)
    else:
        print("\nНеверный выбор")
        interactive_menu()


def main():
    """Главная функция. Разбирает аргументы командной строки."""
    if len(sys.argv) >= 2:
        if sys.argv[1] in ['--check', '-c']:
            if len(sys.argv) >= 3:
                sys.argv[1] = sys.argv[2]
            else:
                sys.argv.pop(1)
            check_mode()
        elif sys.argv[1] in ['--linearize', '-l']:
            if len(sys.argv) >= 3:
                sys.argv[1] = sys.argv[2]
            else:
                sys.argv.pop(1)
            linearize_mode()
        elif sys.argv[1] in ['--help', '-h']:
            print("PDF Linearizer & Checker v2.0\n")
            print("Использование:")
            print("  python LinearizedPDF.py [--check|-c] [файл.pdf]    - Проверка линеаризации")
            print("  python LinearizedPDF.py [--linearize|-l] [файл.pdf] - Линеаризация")
            print("  python LinearizedPDF.py [файл.pdf]                  - Интерактивный режим")
            print("  python LinearizedPDF.py                             - Меню выбора")
            sys.exit(0)
        else:
            interactive_menu()
    else:
        interactive_menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
        sys.exit(0)
