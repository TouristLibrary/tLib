const { test, expect } = require('@playwright/test');
const { gotoMainReady, skipIfSeedMissing } = require('./helpers');

test.describe('Responsive', () => {
  test.setTimeout(120000);

  test('Mobile (375x667) @smoke', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await gotoMainReady(page);
    await expect(page.locator('#searchForm')).toBeVisible();
    await expect(page.locator('#searchButton')).toBeVisible();
  });

  test('Tablet (768x1024)', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await gotoMainReady(page);
    await page.locator('input[name="ГодС"]').fill('2024');
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 30000 });
  });

  test('Desktop (1920x1080)', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await page.setViewportSize({ width: 1920, height: 1080 });
    await gotoMainReady(page, '/?1-TST');
    await expect(page.locator('.single-result-formatted')).toContainText('#1-TST', { timeout: 30000 });
  });
});
