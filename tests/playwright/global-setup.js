// Глобальная предварительная настройка перед запуском E2E-тестов.
// 1. Health-check: сервер должен быть доступен — если нет, прогон прерывается.
// 2. Seed-check: проверяем наличие посевных отчётов, от которых зависят тесты.
//    Отсутствующие сиды передаются в воркеры через TLIB_MISSING_SEEDS (запятые).
//    Прогон НЕ прерывается — зависимые тесты сами себя помечают как SKIPPED
//    с явной причиной через skipIfSeedMissing() из helpers.js.

const { request } = require('@playwright/test');

// Посевные отчёты, наличие которых проверяется перед прогоном.
// Если отчёт отсутствует — зависимые тесты получат explicit skip с причиной.
const SEEDS = [
  {
    id: '1-TST',
    // zip-отчёт — проверяем через archive API (нормализованное имя на диске)
    check: async (ctx, baseURL) => {
      const r = await ctx.get(`${baseURL}/api/archive/00001-TST/contents`, { timeout: 10000 })
        .catch(() => null);
      return r && r.ok();
    },
  },
  {
    id: '842-TLIB',
    // может быть PDF — проверяем наличие записи в БД через поиск
    check: async (ctx, baseURL) => {
      const r = await ctx.post(`${baseURL}/api/search`, {
        form: { 'Шифр': '842', 'ДопШифр': 'TLIB' },
        timeout: 10000,
      }).catch(() => null);
      if (!r || !r.ok()) return false;
      const body = await r.json().catch(() => ({}));
      return Array.isArray(body.data) && body.data.length > 0;
    },
  },
  {
    id: '1-ш',
    // кириллический ДопШифр
    check: async (ctx, baseURL) => {
      const r = await ctx.post(`${baseURL}/api/search`, {
        form: { 'Шифр': '1', 'ДопШифр': 'ш' },
        timeout: 10000,
      }).catch(() => null);
      if (!r || !r.ok()) return false;
      const body = await r.json().catch(() => ({}));
      return Array.isArray(body.data) && body.data.length > 0;
    },
  },
  {
    id: '28466',
    // legacy doc.aspx redirect — присутствует если маппинг есть в redirect_table
    check: async (ctx, baseURL) => {
      const r = await ctx.get(`${baseURL}/doc.aspx?id=28466`, {
        timeout: 10000,
        maxRedirects: 0,
      }).catch(() => null);
      if (!r) return false;
      // Любой 3xx с Location без notfound=1 — маппинг существует.
      // Принимаем весь диапазон 3xx: устойчиво к изменению REDIRECT_STATUS_CODE (301/302/307/308).
      const loc = r.headers()['location'] || '';
      return r.status() >= 300 && r.status() < 400 && !loc.includes('notfound');
    },
  },
];

module.exports = async (config) => {
  const baseURL = config.projects[0].use.baseURL;
  const ctx = await request.newContext();

  try {
    // 1. Health-check — обязательный, прерывает прогон при недоступности сервера
    const resp = await ctx.get(`${baseURL}/health`, { timeout: 5000 });
    if (!resp.ok()) {
      throw new Error(`Health check failed: HTTP ${resp.status()}`);
    }

    // 2. Seed-check — не прерывает прогон, только собирает список отсутствующих
    const missing = [];
    for (const seed of SEEDS) {
      const present = await seed.check(ctx, baseURL);
      if (!present) missing.push(seed.id);
    }

    if (missing.length > 0) {
      const border = '='.repeat(70);
      console.warn(`\n${border}`);
      console.warn('ВНИМАНИЕ: ПОСЕВНЫЕ ОТЧЁТЫ ОТСУТСТВУЮТ НА СТЕНДЕ');
      console.warn(`Стенд: ${baseURL}`);
      console.warn(`Отсутствуют: ${missing.join(', ')}`);
      console.warn('Зависимые тесты будут пропущены (SKIPPED) с явной причиной.');
      console.warn('Засейте фикстуры через File Watcher (data.up/20_go/).');
      console.warn(`${border}\n`);
      // Передаём список воркерам — env наследуется дочерними процессами
      process.env.TLIB_MISSING_SEEDS = missing.join(',');
    } else {
      process.env.TLIB_MISSING_SEEDS = '';
    }

  } catch (e) {
    throw new Error(
      `Server at ${baseURL} is unavailable. Start the server before running tests.\n` + e.message
    );
  } finally {
    await ctx.dispose();
  }
};
