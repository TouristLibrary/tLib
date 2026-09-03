const { test, expect } = require('@playwright/test');
const { gotoMainReady, selectDopShifr, skipIfSeedMissing } = require('./helpers');

test.describe('Critical path', () => {
  test.setTimeout(120000);

  test('Загрузка главной страницы @critical @smoke', async ({ page }) => {
    await gotoMainReady(page);
    await expect(page.locator('#searchForm')).toBeVisible();
    await expect(page.locator('#searchButton')).toBeVisible();
    await expect(page.locator('#clearFormBtn')).toBeVisible();
    await expect(page.locator('#publishReportBtn')).toBeVisible();
    await expect(page.locator('#sidebarAuthBtn')).toBeVisible();
    await expect(page.locator('#publishReportBtn')).toHaveAttribute('aria-disabled', 'true');
    await expect(page.locator('.auth-icon-login')).toBeVisible();
  });

  test('Поиск с фильтром (таблица результатов) @critical @smoke', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('input[name="ГодС"]').fill('2024');
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 15000 });
    const rows = page.locator('#results-table tbody tr');
    await expect(rows.first()).toBeVisible({ timeout: 5000 });
  });

  test('Сортировка таблицы @critical @smoke', async ({ page }) => {
    await gotoMainReady(page);
    await expect(page.locator('#sortSelect')).toBeVisible();
    await expect(page.locator('#sortDirectionBtn')).toBeVisible();
    await page.locator('#sortSelect').selectOption('Категория');
    await page.locator('input[name="ГодС"]').fill('2024');
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 15000 });
    await page.locator('#sortDirectionBtn').click();
    await expect(page.locator('#sortOrderInput')).toHaveValue('asc');
  });

  test('Поиск конкретного отчёта 1-TST @critical @smoke', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await gotoMainReady(page);
    await page.locator('#clearFormBtn').click();
    await page.locator('input[name="Шифр"]').fill('1');
    await selectDopShifr(page, 'TST');
    await page.locator('#searchButton').click();
    await expect(page.locator('.single-result-formatted')).toContainText('#1-TST', { timeout: 10000 });
    await expect(page).toHaveURL(/1-TST/);
  });
});
