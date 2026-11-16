// Проверка ZIP до отправки отчёта.
// Читает только сигнатуру, оглавление и заголовки записей (file.slice), содержимое не распаковывает.
// Ловит RAR/7z под видом ZIP, обрезанный архив и расхождение имени в оглавлении и заголовке —
// те же случаи, из-за которых Python отказывается открывать архив.
// ZIP64 и собственная ошибка разбора возвращают пустую строку: решение остаётся за сервером,
// чтобы сбой этой проверки не запрещал загрузку исправного файла.

import { MESSAGES } from '../config/messages.config.js';

const ZIP_CHECK = MESSAGES.UPLOAD_ZIP_CHECK;
const UTF8_FLAG = 0x800;
const U16_MAX = 0xffff;
const U32_MAX = 0xffffffff;

function u16(bytes, offset) {
    return bytes[offset] | (bytes[offset + 1] << 8);
}

function u32(bytes, offset) {
    return (bytes[offset]
        | (bytes[offset + 1] << 8)
        | (bytes[offset + 2] << 16)
        | (bytes[offset + 3] << 24)) >>> 0;
}

function startsWith(bytes, signature) {
    if (!bytes || bytes.length < signature.length) return false;
    for (let i = 0; i < signature.length; i++) {
        if (bytes[i] !== signature[i]) return false;
    }
    return true;
}

function sameBytes(left, right) {
    if (left.length !== right.length) return false;
    for (let i = 0; i < left.length; i++) {
        if (left[i] !== right[i]) return false;
    }
    return true;
}

function hasNonAscii(bytes) {
    for (let i = 0; i < bytes.length; i++) {
        if (bytes[i] >= 0x80) return true;
    }
    return false;
}

/** Имя для сообщения автору: UTF-8, если флаг стоит, иначе DOS-кириллица старых архивов. */
function decodeEntryName(bytes, utf8) {
    const text = new TextDecoder(utf8 ? 'utf-8' : 'ibm866').decode(bytes).replace(/\/+$/, '');
    return text || 'без имени';
}

async function readRange(file, start, length) {
    if (start < 0 || length < 0 || start + length > file.size) return null;
    return new Uint8Array(await file.slice(start, start + length).arrayBuffer());
}

/** Конец оглавления у самого конца файла. Чужой PK\x05\x06 внутри комментария не подходит: длина комментария должна доходить ровно до конца. */
function findEocd(tail) {
    for (let i = tail.length - 22; i >= 0; i--) {
        if (tail[i] !== 0x50 || tail[i + 1] !== 0x4b || tail[i + 2] !== 0x05 || tail[i + 3] !== 0x06) {
            continue;
        }
        if (i + 22 + u16(tail, i + 20) === tail.length) return i;
    }
    return -1;
}

/**
 * Проверяет выбранный файл, если это .zip.
 *
 * @param {File} file
 * @returns {Promise<string>} пустая строка, если грузить можно; иначе текст для автора
 */
export async function checkZipFile(file) {
    try {
        if (!file || !/\.zip$/i.test(file.name || '')) return '';
        if (!file.size) return ZIP_CHECK.NOT_ZIP;

        const head = await readRange(file, 0, Math.min(8, file.size));
        if (!head) return '';
        if (startsWith(head, [0x52, 0x61, 0x72, 0x21])) return ZIP_CHECK.RAR;
        if (startsWith(head, [0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c])) return ZIP_CHECK.SEVEN_ZIP;
        if (head[0] !== 0x50 || head[1] !== 0x4b) return ZIP_CHECK.NOT_ZIP;

        const tailLen = Math.min(file.size, 22 + 65535);
        const tail = await readRange(file, file.size - tailLen, tailLen);
        if (!tail) return '';
        const eocdAt = findEocd(tail);
        if (eocdAt < 0) return ZIP_CHECK.TRUNCATED;

        const entries = u16(tail, eocdAt + 10);
        const cdSize = u32(tail, eocdAt + 12);
        const cdOffset = u32(tail, eocdAt + 16);
        // ZIP64: смещения не влезают в обычные поля. Такой архив не угадываем — его проверит сервер
        if (entries === U16_MAX || cdSize === U32_MAX || cdOffset === U32_MAX) return '';
        if (cdOffset + cdSize > file.size) return ZIP_CHECK.TRUNCATED;

        const central = await readRange(file, cdOffset, cdSize);
        if (!central || central.length !== cdSize) return ZIP_CHECK.TRUNCATED;

        let pos = 0;
        let seen = 0;
        while (pos + 46 <= central.length && seen < entries) {
            if (central[pos] !== 0x50 || central[pos + 1] !== 0x4b
                || central[pos + 2] !== 0x01 || central[pos + 3] !== 0x02) {
                return ZIP_CHECK.TRUNCATED;
            }
            const flags = u16(central, pos + 8);
            const nameLen = u16(central, pos + 28);
            const extraLen = u16(central, pos + 30);
            const commentLen = u16(central, pos + 32);
            const localOffset = u32(central, pos + 42);
            if (nameLen === U16_MAX || extraLen === U16_MAX || commentLen === U16_MAX
                || localOffset === U32_MAX) {
                return '';
            }
            if (pos + 46 + nameLen > central.length) return ZIP_CHECK.TRUNCATED;
            const centralName = central.subarray(pos + 46, pos + 46 + nameLen);

            const localHead = await readRange(file, localOffset, 30);
            if (!localHead || localHead.length < 30
                || localHead[0] !== 0x50 || localHead[1] !== 0x4b
                || localHead[2] !== 0x03 || localHead[3] !== 0x04) {
                return ZIP_CHECK.TRUNCATED;
            }
            const localFlags = u16(localHead, 6);
            const localNameLen = u16(localHead, 26);
            if (localNameLen === U16_MAX) return '';
            const localName = await readRange(file, localOffset + 30, localNameLen);
            if (!localName || localName.length !== localNameLen) return ZIP_CHECK.TRUNCATED;

            const utfFlagDiffers = ((flags ^ localFlags) & UTF8_FLAG) !== 0;
            const nonAscii = hasNonAscii(centralName) || hasNonAscii(localName);
            if (!sameBytes(centralName, localName) || (utfFlagDiffers && nonAscii)) {
                const shown = decodeEntryName(centralName, (flags & UTF8_FLAG) !== 0);
                return ZIP_CHECK.NAME_MISMATCH.replace('{name}', shown);
            }

            pos += 46 + nameLen + extraLen + commentLen;
            seen++;
        }
        if (seen !== entries) return ZIP_CHECK.TRUNCATED;
        return '';
    } catch {
        return '';
    }
}
