const { test, expect } = require('@playwright/test');
const { gotoMainReady, selectDopShifr, skipIfSeedMissing } = require('./helpers');

test.describe('Security', () => {
  test.setTimeout(120000);

  // --- Базовые проверки безопасности (из smoke-27) ---

  test('Консоль без ошибок @critical @smoke @security', async ({ page }) => {
    const errors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await gotoMainReady(page);
    const criticalErrors = errors.filter((e) => !e.includes('extension') && !e.includes('favicon'));
    expect(criticalErrors.length).toBe(0);
  });

  test('XSS protection (script в поле маршрута) @smoke @security', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('textarea[name="Маршрут"]').fill('<script>alert(1)</script>');
    const responsePromise = page.waitForResponse(r => r.url().includes('/api/search'));
    await page.locator('#searchButton').click();
    await responsePromise;
    await expect(page.locator('#searchForm')).toBeVisible();
  });

  test('SQL Injection @security', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('input[name="Автор"]').fill("' OR '1'='1");
    const responsePromise = page.waitForResponse(r => r.url().includes('/api/search'));
    await page.locator('#searchButton').click();
    await responsePromise;
    await expect(page.locator('#searchForm')).toBeVisible();
  });

  // --- DOM XSS проверки (из xss-protection) ---

  test('Нет неэкранированных script/iframe в таблице @security', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 10000 });

    const suspiciousElements = await page.locator('#results-table script, #results-table iframe').count();
    expect(suspiciousElements).toBe(0);
  });

  test('Нет неэкранированных script/iframe в карточке отчёта @security', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await gotoMainReady(page);
    await page.locator('input[name="Шифр"]').fill('1');
    await selectDopShifr(page, 'TST');
    await page.locator('#searchButton').click();

    const card = page.locator('.single-result-formatted');
    await expect(card).toBeVisible({ timeout: 10000 });

    const suspiciousElements = await card.locator('script, iframe').count();
    expect(suspiciousElements).toBe(0);
  });

  test('DOM: innerHTML ячеек таблицы не содержит неэкранированных тегов @security', async ({ page }) => {
    await gotoMainReady(page);
    const responsePromise = page.waitForResponse((r) => r.url().includes('/api/search'));
    await page.locator('#searchButton').click();
    await responsePromise;
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 10000 });

    const isEscaped = await page.evaluate(() => {
      const cells = document.querySelectorAll('#results-table tbody td');
      for (let i = 0; i < Math.min(cells.length, 20); i++) {
        const html = cells[i].innerHTML;
        if (html.includes('<script') && !html.includes('&lt;script')) return false;
        if (html.includes('<iframe') && !html.includes('&lt;iframe')) return false;
      }
      return true;
    });

    expect(isEscaped).toBe(true);
  });

  test('Нет неожиданных глобальных переменных из данных @security', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 10000 });

    const hasUnexpectedGlobals = await page.evaluate(() => {
      const suspicious = ['hacked', 'pwned', 'xss'];
      for (const key of suspicious) {
        if (window[key] !== undefined) return true;
      }
      return false;
    });

    expect(hasUnexpectedGlobals).toBe(false);
  });
});
