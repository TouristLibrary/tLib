// Общие хелперы E2E-тестов TlibWebApp.
// Два независимых контракта готовности главной страницы:
//   1. gotoMainReady — API готово (#searchButton enabled)
//   2. selectDopShifr — справочники загружены (фоновый ReferenceListsService)
// Разделены намеренно: не все тесты выбирают ДопШифр.
// 3. skipIfSeedMissing — явный пропуск теста если посевной отчёт не засеян на стенде.

const { expect } = require('@playwright/test');

/**
 * Переходит на страницу и дожидается готовности API (кнопка поиска активна).
 * Заменяет повторяющийся паттерн `goto + waitForSelector('#searchButton:not([disabled])')`.
 * Для выбора ДопШифр дополнительно вызывай selectDopShifr — справочники загружаются
 * фоном и могут прийти позже, чем включится кнопка.
 */
async function gotoMainReady(page, url = '/') {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#searchButton:not([disabled])', { timeout: 60000 });
}

/**
 * Дожидается появления опции value в select[name="ДопШифр"] (фоновая загрузка справочника)
 * и выбирает её. Устраняет гонку: кнопка поиска и справочники — два независимых потока
 * инициализации, поэтому дождавшись кнопки, опции в select могут ещё не прийти.
 */
async function selectDopShifr(page, value) {
  await expect(
    page.locator(`select[name="ДопШифр"] option[value="${value}"]`)
  ).toHaveCount(1, { timeout: 30000 });
  await page.locator('select[name="ДопШифр"]').selectOption({ value });
}

/**
 * Пропускает тест с явной причиной если посевной отчёт id отсутствует на стенде.
 * Список отсутствующих сидов устанавливается global-setup.js в TLIB_MISSING_SEEDS.
 * Вызывать первой строкой в теле теста, зависящего от конкретного отчёта.
 *
 * @param {import('@playwright/test').TestType<{}, {}>} test - объект test из Playwright
 * @param {string} id - идентификатор сида, например '1-TST', '842-TLIB'
 */
function skipIfSeedMissing(test, id) {
  const missing = (process.env.TLIB_MISSING_SEEDS || '').split(',').filter(Boolean);
  test.skip(
    missing.includes(id),
    `Сид ${id} отсутствует на стенде — засейте фикстуру через File Watcher (data.up/20_go/).`
  );
}

module.exports = { gotoMainReady, selectDopShifr, skipIfSeedMissing };
