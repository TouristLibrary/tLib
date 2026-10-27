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

  // Headless-боты исполняют JS, но не дают жестов: без жеста фронтенд не должен
  // отправлять запросы, запускающие конвертацию (/prepare без probe и /resolve).
  // GET /pages до жеста допустим — кэшированный PDF показывается сразу (окно рендера).
  test('Без жеста пользователя конвертация не запускается @security', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');

    /** @type {string[]} */
    const starters = [];
    page.on('request', (req) => {
      const url = req.url();
      if (req.method() !== 'POST' || !url.includes('/api/cache/')) return;
      if (url.includes('/resolve') || (url.includes('/prepare') && !url.includes('probe=1'))) {
        starters.push(url);
      }
    });

    await page.goto('/?1-TST#tab=pdf', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.tab-button.active[data-tab="tab-1"]')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('#tab-1 .tab-link[data-pdf-url]').first()).toBeVisible({ timeout: 15000 });

    await page.waitForTimeout(5000);
    expect(starters, starters.join('\n')).toEqual([]);

    // Кеша нет (нет data-pages-total) — вместо спиннера кнопка «Открыть отчёт»; кеш есть — вьюер без неё
    const openBtn = page.locator('#tab-1 .viewer-container .gesture-open-btn');
    const pagesTotal = await page.locator('#tab-1 .viewer-container').first().getAttribute('data-pages-total');
    if (pagesTotal) {
      await expect(openBtn).toHaveCount(0);
    } else {
      await expect(openBtn).toBeVisible();
    }

    await page.mouse.move(100, 100);
    await page.mouse.move(200, 200);
    await expect.poll(() => starters.length, { timeout: 10000 }).toBeGreaterThan(0);
    await expect(openBtn).toHaveCount(0);
  });

  // Вкладка «Карта 3D» (kartoid.com): стоит сразу после «Треков»; кеш готовит resolve, а он ждёт жест,
  // поэтому до жеста у фрейма нет src, а ссылка в редактор выключена. Содержимое фрейма не проверяем:
  // Kartoid забирает файл через свой прокси по таблице хостов, со стенда по внутреннему адресу он его не получит.
  test('Вкладка «Карта 3D»: после «Треков», фрейм и ссылка в редактор только после жеста', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await page.goto('/?1-TST#tab=map', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.tab-button.active[data-tab="tab-5"]')).toBeVisible({ timeout: 15000 });

    const labels = (await page.locator('.tabs-container .tab-button').allInnerTexts()).map((t) => t.trim());
    const geoIndex = labels.findIndex((t) => t.startsWith('Треки'));
    expect(geoIndex, labels.join(' | ')).toBeGreaterThanOrEqual(0);
    expect(labels[geoIndex + 1], labels.join(' | ')).toMatch(/^Карта 3D/);

    // До жеста: фрейм без src, ссылка в редактор выключена
    const frame = page.locator('#tab-5 iframe');
    const editorLink = page.locator('#tab-5 a[href^="https://kartoid.com/map/"]');
    await page.waitForTimeout(3000);
    await expect(frame).toHaveAttribute('data-pending-url', /^https:\/\/kartoid\.com\/track#/);
    await expect(frame).not.toHaveAttribute('src', /.+/);
    await expect(editorLink).toHaveAttribute('data-pending-track', 'kartoid');

    await page.mouse.move(100, 100);
    await page.mouse.move(200, 200);
    await expect(frame).toHaveAttribute('src', /^https:\/\/kartoid\.com\/track#/, { timeout: 30000 });
    await expect(editorLink).not.toHaveAttribute('data-pending-track', 'kartoid');

    // Одна ссылка на готовый файл в /cache/, всё в hash, без query и без fail
    const origin = new URL(page.url()).origin;
    const geoZip = `${origin}/cache/00001-TST/00001-TST-geo.zip`;
    const frameUrl = new URL(await frame.getAttribute('src'));
    expect(frameUrl.search).toBe('');
    const frameParams = new URLSearchParams(frameUrl.hash.slice(1));
    expect(frameParams.get('p')).toBe(geoZip);
    expect(frameParams.has('fail')).toBe(false);

    const editorUrl = new URL(await editorLink.getAttribute('href'));
    expect(editorUrl.search).toBe('');
    expect(new URLSearchParams(editorUrl.hash.slice(1)).get('p')).toBe(geoZip);

    // Права фрейма не расширены
    await expect(frame).not.toHaveAttribute('allow', /.*/);
    await expect(frame).not.toHaveAttribute('sandbox', /.*/);
  });

  test('Отчёт без файла: вкладки «Карта 3D» нет', async ({ page }) => {
    skipIfSeedMissing(test, '842-TLIB');
    await page.goto('/?842-TLIB', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#842-TLIB', { timeout: 15000 });
    await expect(page.locator('.tab-button[data-tab="tab-5"]')).toHaveCount(0);
    await expect(page.locator('iframe[src*="kartoid.com"], iframe[data-pending-url*="kartoid.com"]')).toHaveCount(0);
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
