// Проверка ZIP на странице загрузки (js/modules/zipCheck.js).
// Авторизация не нужна: модуль читает только байты файла в браузере.
// Четыре случая собраны здесь же — нормальный ZIP, RAR, обрезанный архив и расхождение имени.

const { test, expect } = require('@playwright/test');
const zlib = require('zlib');

function u16(value) {
  const buf = Buffer.alloc(2);
  buf.writeUInt16LE(value);
  return buf;
}

function u32(value) {
  const buf = Buffer.alloc(4);
  buf.writeUInt32LE(value >>> 0);
  return buf;
}

/** ZIP без сжатия: одна запись, оглавление и конец оглавления. */
function storedZip(name, payload) {
  const nameBuf = Buffer.from(name, 'utf8');
  const crc = zlib.crc32(payload) >>> 0;
  const local = Buffer.concat([
    Buffer.from('PK\x03\x04'),
    u16(20), u16(0), u16(0), u16(0), u16(0),
    u32(crc), u32(payload.length), u32(payload.length),
    u16(nameBuf.length), u16(0),
    nameBuf,
    payload,
  ]);
  const central = Buffer.concat([
    Buffer.from('PK\x01\x02'),
    u16(20), u16(20), u16(0), u16(0), u16(0), u16(0),
    u32(crc), u32(payload.length), u32(payload.length),
    u16(nameBuf.length), u16(0), u16(0),
    u16(0), u16(0), u32(0),
    u32(0),
    nameBuf,
  ]);
  const eocd = Buffer.concat([
    Buffer.from('PK\x05\x06'),
    u16(0), u16(0), u16(1), u16(1),
    u32(central.length), u32(local.length),
    u16(0),
  ]);
  return Buffer.concat([local, central, eocd]);
}

test.describe('Проверка ZIP при выборе файла', () => {
  test('RAR, обрезка и чужое имя в заголовке не проходят, обычный ZIP проходит', async ({ page }) => {
    const good = storedZip('otchet.txt', Buffer.from('hello'));
    const mismatch = Buffer.from(good);
    mismatch[30] ^= 0x01;
    const samples = {
      good: [...good],
      rar: [...Buffer.from('Rar!\x1a\x07\x00')],
      truncated: [...good.subarray(0, good.length - 22)],
      mismatch: [...mismatch],
    };

    await page.goto('/', { waitUntil: 'commit' });
    const result = await page.evaluate(async (packed) => {
      const { checkZipFile } = await import('/js/modules/zipCheck.js');
      const out = {};
      for (const [key, bytes] of Object.entries(packed)) {
        const file = new File([new Uint8Array(bytes)], 'report.zip');
        out[key] = await checkZipFile(file);
      }
      return out;
    }, samples);

    expect(result.good).toBe('');
    expect(result.rar).toContain('RAR');
    expect(result.truncated).toContain('повреждён');
    expect(result.mismatch).toContain('otchet.txt');
  });
});
