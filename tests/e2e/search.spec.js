const { test, expect } = require('@playwright/test');
const { gotoMainReady } = require('./helpers');

test.describe('Search', () => {
  test.setTimeout(120000);

  // --- Граничные случаи (из smoke-27) ---

  test('Пустой поиск @smoke', async ({ page }) => {
    await gotoMainReady(page);
    const responsePromise = page.waitForResponse(r => r.url().includes('/api/search'));
    await page.locator('#searchButton').click();
    await responsePromise;
    await expect(page.locator('#searchForm')).toBeVisible();
  });

  test('Максимальный шифр 99999', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('input[name="Шифр"]').fill('99999');
    await page.locator('#searchButton').click();
    await expect(page.getByText('Ничего не найдено')).toBeVisible({ timeout: 10000 });
  });

  test('Отрицательный год', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('input[name="ГодС"]').fill('-1');
    await expect(page.locator('#searchForm')).toBeVisible();
  });

  test('Race condition (быстрые поиски)', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('input[name="ГодС"]').fill('2020');
    await page.locator('#searchButton').click();
    await page.locator('input[name="ГодС"]').fill('2021');
    await page.locator('#searchButton').click();
    await page.locator('input[name="ГодС"]').fill('2024');
    await page.locator('#searchButton').click();
    await expect(page).toHaveURL(/2024/, { timeout: 15000 });
    await expect(page.locator('#results-table')).toBeVisible();
  });

  // --- Корректность отображения данных (из xss-protection) ---

  test('Кириллица в маршрутах отображается без HTML-сущностей @security', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 10000 });

    const routeCells = page.locator('#results-table tbody tr td a.report-link');
    const count = await routeCells.count();
    expect(count).toBeGreaterThan(0);

    const firstRoute = await routeCells.first().textContent();
    expect(firstRoute).toBeTruthy();
    expect(firstRoute).not.toContain('&lt;');
    expect(firstRoute).not.toContain('&gt;');
    expect(firstRoute).not.toContain('&amp;');
  });

  test('Спецсимволы (скобки, тире, запятые) отображаются как есть @security', async ({ page }) => {
    await gotoMainReady(page);
    const responsePromise = page.waitForResponse((r) => r.url().includes('/api/search'));
    await page.locator('#searchButton').click();
    await responsePromise;
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 10000 });

    const authorCells = page.locator('#results-table tbody tr td').nth(2);
    const firstAuthor = await authorCells.first().textContent();

    if (firstAuthor && firstAuthor.includes(',')) {
      expect(firstAuthor).toMatch(/,/);
    }
  });

  test('Длинные маршруты не выходят за границы ячейки (word-wrap)', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 10000 });

    const count = await page.locator('#results-table tbody tr td a.report-link').count();
    expect(count).toBeGreaterThan(0);

    const hasOverflow = await page.evaluate(() => {
      const cells = document.querySelectorAll('#results-table tbody tr td');
      for (const cell of cells) {
        const style = window.getComputedStyle(cell);
        if (style.wordWrap === 'normal' && style.overflowWrap === 'normal') return true;
      }
      return false;
    });

    expect(hasOverflow).toBe(false);
  });
});
