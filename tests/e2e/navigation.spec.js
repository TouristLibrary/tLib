const { test, expect } = require('@playwright/test');
const { gotoMainReady, skipIfSeedMissing } = require('./helpers');

test.describe('Navigation', () => {
  test.setTimeout(120000);

  test('Редирект компактный (842-TLIB) @critical @smoke', async ({ page }) => {
    skipIfSeedMissing(test, '842-TLIB');
    await page.goto('/?842-TLIB', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#842-TLIB', { timeout: 15000 });
  });

  test('Редирект полный формат URL', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await page.goto('/?Шифр=1&ДопШифр=TST', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#1-TST', { timeout: 15000 });
    await expect(page.locator('input[name="Шифр"]')).toHaveValue('1');
    // Значение ДопШифр проставляется фоновым ReferenceListsService — ждём устойчивого состояния
    await expect
      .poll(() => page.locator('select[name="ДопШифр"]').inputValue(), { timeout: 10000 })
      .toMatch(/TST/);
  });

  test('Редирект кириллица (1-ш)', async ({ page }) => {
    skipIfSeedMissing(test, '1-ш');
    await page.goto('/?1-%D1%88', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#1-ш', { timeout: 15000 });
  });

  test('Legacy doc.aspx redirect @smoke', async ({ page }) => {
    skipIfSeedMissing(test, '28466');
    await page.goto('/doc.aspx?id=28466&page=1', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.tab-button.active[data-tab="tab-1"]')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('#tab-1 .tab-link[data-pdf-url]').first()).toBeVisible({ timeout: 15000 });

    // Регрессия: URL не должен «съезжать» с p=1 из-за scroll-echo / reflow при дозагрузке PNG
    await expect.poll(
      () => new URL(page.url()).hash,
      { timeout: 5000, intervals: [200, 500, 1000] }
    ).toBe('#tab=pdf&p=1');

    await page.waitForTimeout(1000);
    expect(new URL(page.url()).hash).toBe('#tab=pdf&p=1');
  });

  test('Not found (999999999)', async ({ page }) => {
    await page.goto('/doc.aspx?id=999999999', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Ничего не найдено')).toBeVisible({ timeout: 10000 });
  });

  test('Plain-mode', async ({ page }) => {
    skipIfSeedMissing(test, '842-TLIB');
    await page.goto('/?842-TLIB#tab=pdf&p=1&plain=1', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('html.plain-mode')).toBeVisible({ timeout: 5000 });
  });

  test('Навигация назад', async ({ page }) => {
    await gotoMainReady(page);
    await page.locator('input[name="ГодС"]').fill('2024');
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 15000 });
    const link = page.locator('#results-table tbody tr td a.report-link:not(.shifr-link)').first();
    await expect(link).toBeVisible();
    await link.click();
    await expect(page.locator('.single-result-formatted')).toBeVisible({ timeout: 15000 });
    await page.goBack();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 5000 });
  });
});
