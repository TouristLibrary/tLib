const { test, expect } = require('@playwright/test');
const { gotoMainReady, selectDopShifr, skipIfSeedMissing } = require('./helpers');

test.describe('Report', () => {
  test.setTimeout(120000);

  // --- Функциональность карточки отчёта (из smoke-27) ---

  test('Переключение табов (PDF, Изображения) @smoke', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await page.goto('/?1-TST', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#1-TST', { timeout: 15000 });
    await page.locator('[data-tab="tab-1"]').click();
    await expect(page.locator('#tab-1 .tab-link[data-pdf-url]').first()).toBeVisible({ timeout: 15000 });
    await page.locator('[data-tab="tab-3"]').click();
    await expect(page.locator('.tab-link[data-image-url]').first()).toBeVisible({ timeout: 10000 });
  });

  test('Очистка формы', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('input[name="Шифр"]').fill('123');
    await selectDopShifr(page, 'TST');
    await page.locator('#clearFormBtn').click();
    await expect(page.locator('input[name="Шифр"]')).toHaveValue('');
    await expect(page).not.toHaveURL(/\?/);
  });

  test('Кнопки Поделиться и Скачать', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await page.goto('/?1-TST', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#1-TST', { timeout: 15000 });
    await expect(page.locator('.copy-report-link')).toBeVisible();
    await expect(page.locator('a[href*="/data/00001-TST.zip"]')).toBeVisible();
  });

  test('Скачивание файлов', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await page.goto('/?1-TST', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#1-TST', { timeout: 15000 });
    const downloadLink = page.locator('a[href*="/data/00001-TST.zip"]');
    await expect(downloadLink).toBeVisible();
    await expect(downloadLink).toHaveAttribute('href', /\/data\/00001-TST\.zip/);
  });

  // --- Корректность отображения карточки (из xss-protection) ---

  test('Карточка единственного результата: нет HTML-сущностей в тексте', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await gotoMainReady(page);
    await page.locator('input[name="Шифр"]').fill('1');
    await selectDopShifr(page, 'TST');
    await page.locator('#searchButton').click();

    const card = page.locator('.single-result-formatted');
    await expect(card).toBeVisible({ timeout: 10000 });
    await expect(card.locator('.single-result-field').first()).toBeVisible();

    const cardText = await card.textContent();
    expect(cardText).not.toContain('&lt;');
    expect(cardText).not.toContain('&gt;');
  });

  test('Табы переключаются без ошибок консоли', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    const consoleErrors = [];
    const pageErrors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error')
        consoleErrors.push({ text: msg.text(), url: msg.location()?.url || '' });
    });
    // Необработанные JS-исключения — самый сильный сигнал поломки
    page.on('pageerror', (err) => pageErrors.push(err.message));

    await gotoMainReady(page);
    await page.locator('input[name="Шифр"]').fill('1');
    await selectDopShifr(page, 'TST');
    await page.locator('#searchButton').click();

    await expect(page.locator('.tabs-container')).toBeVisible({ timeout: 10000 });

    const tabs = page.locator('.tab-button');
    const tabCount = await tabs.count();
    expect(tabCount).toBeGreaterThan(0);

    for (let i = 0; i < Math.min(tabCount, 3); i++) {
      await tabs.nth(i).click();
      await page.waitForTimeout(300);
      await expect(tabs.nth(i)).toHaveClass(/active/);
    }

    const origin = new URL(page.url()).origin;
    const isBenign = (e) =>
      // шум расширений браузера
      e.text.includes('extension') ||
      e.text.includes('chrome-extension') ||
      // штатный 401 анонима (authService.getCurrentUser): /api/auth/me → 401 по контракту
      e.url.includes('/api/auth/me') ||
      // резервный матч: в части сборок Chromium location().url может быть пустым
      (e.url === '' && e.text.includes('Failed to load resource') && e.text.includes('401')) ||
      // чужой origin — шум из iframe nakarte.me на вкладке «Треки»
      (e.url !== '' && !e.url.startsWith(origin));

    const criticalConsole = consoleErrors.filter((e) => !isBenign(e));

    // Ни необработанных исключений, ни «своих» console-ошибок
    expect(pageErrors, pageErrors.join('\n')).toEqual([]);
    expect(criticalConsole, JSON.stringify(criticalConsole, null, 2)).toEqual([]);
  });

  test('Отчёт без файлов: иконки скачивания отображаются как disabled', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 10000 });

    const disabledIcons = page.locator('.shifr-action-icon.disabled');
    const count = await disabledIcons.count();

    if (count > 0) {
      await expect(disabledIcons.first()).toBeVisible();
    }
  });

  // --- Sidebar (из tlib) ---

  test('Sidebar collapse/expand toggles main-content class (desktop)', async ({ page }) => {
    test.setTimeout(120000);
    await page.setViewportSize({ width: 1400, height: 900 });
    await gotoMainReady(page);

    const main = page.locator('.main-content');
    await expect(main).toBeVisible();

    await page.locator('.search-panel').hover();
    await page.evaluate(() => document.getElementById('sidebarCollapseBtn')?.click());
    await expect(main).toHaveClass(/sidebar-collapsed/);

    await page.locator('.search-panel').hover();
    await page.evaluate(() => document.getElementById('sidebarExpandBtn')?.click());
    await expect(main).not.toHaveClass(/sidebar-collapsed/);
  });
});
