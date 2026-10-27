// Вкладка «Карта 3D» (kartoid.com, js/modules/ui/results/viewers/kartoidViewer.js).
// Фрейму уходит одна ссылка на готовый файл в /cache/ в hash адреса: общий архив треков или единственный трек;
// при отказе сервера по общему архиву — первый GPX/KML/KMZ; не вышло и с ним — fail=<код> для Kartoid.
// Кеш готовит resolve, а он ждёт жест пользователя, поэтому до жеста у фрейма нет src, а ссылка в редактор выключена.
// Содержимое фрейма не проверяем: Kartoid забирает файл своим прокси по таблице хостов,
// со стенда по внутреннему адресу он его не получит. Отказы сервера подменяются через page.route.

const { test, expect } = require('@playwright/test');
const { skipIfSeedMissing } = require('./helpers');

/** Параметры из hash адреса Kartoid */
const hashParams = (url) => new URLSearchParams(new URL(url).hash.slice(1));

/** Жест мышью: без него resolve не уходит (js/utils/userGesture.js) */
async function gesture(page) {
  await page.mouse.move(100, 100);
  await page.mouse.move(200, 200);
}

/** Открывает 1-TST сразу на вкладке карты */
async function openMapTab(page) {
  await page.goto('/?1-TST#tab=map', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.tab-button.active[data-tab="tab-5"]')).toBeVisible({ timeout: 15000 });
}

test.describe('Карта 3D', () => {
  test.setTimeout(120000);

  test('Место после «Треков», фрейм и ссылка в редактор только после жеста', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await openMapTab(page);

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

    await gesture(page);
    await expect(frame).toHaveAttribute('src', /^https:\/\/kartoid\.com\/track#/, { timeout: 30000 });
    await expect(editorLink).not.toHaveAttribute('data-pending-track', 'kartoid');

    // Одна ссылка на готовый файл в /cache/, всё в hash, без query и без fail
    const origin = new URL(page.url()).origin;
    const geoZip = `${origin}/cache/00001-TST/00001-TST-geo.zip`;
    const frameUrl = new URL(await frame.getAttribute('src'));
    expect(frameUrl.search).toBe('');
    const p = hashParams(frameUrl.href).get('p');
    expect(p).toBe(geoZip);
    expect(p).not.toContain('/api/archive/');
    expect(p).not.toContain('original=1');
    expect(hashParams(frameUrl.href).has('fail')).toBe(false);

    const editorUrl = new URL(await editorLink.getAttribute('href'));
    expect(editorUrl.search).toBe('');
    expect(hashParams(editorUrl.href).get('p')).toBe(geoZip);

    // Права фрейма не расширены
    await expect(frame).not.toHaveAttribute('allow', /.*/);
    await expect(frame).not.toHaveAttribute('sandbox', /.*/);
  });

  test('Архив без треков: нет ни «Треков», ни «Карты 3D»', async ({ page }) => {
    skipIfSeedMissing(test, '3287-STK');
    await page.goto('/?3287-STK', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#3287-STK', { timeout: 15000 });
    await expect(page.locator('.tab-button[data-tab="tab-1"]')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('.tab-button[data-tab="tab-2"]')).toHaveCount(0);
    await expect(page.locator('.tab-button[data-tab="tab-5"]')).toHaveCount(0);
  });

  test('Треки только в PLT и WPT: «Треки» есть, «Карты 3D» нет', async ({ page }) => {
    skipIfSeedMissing(test, '463-TLIB');
    await page.goto('/?463-TLIB', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.single-result-formatted')).toContainText('#463-TLIB', { timeout: 15000 });
    await expect(page.locator('.tab-button[data-tab="tab-2"]')).toBeVisible({ timeout: 15000 });

    const trackNames = await page.locator('#tab-2 .tab-link[data-track-name]')
      .evaluateAll((els) => els.map((e) => e.getAttribute('data-track-name')));
    expect(trackNames.some((n) => n.toLowerCase().endsWith('.plt')), trackNames.join(' | ')).toBe(true);

    await expect(page.locator('.tab-button[data-tab="tab-5"]')).toHaveCount(0);
    await expect(page.locator('iframe[src*="kartoid.com"], iframe[data-pending-url*="kartoid.com"]')).toHaveCount(0);
  });

  test('Общего архива треков нет: фрейм и редактор получают первый трек', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    /** @type {{kind: string, path: string}[]} */
    const resolves = [];
    await page.route('**/api/cache/00001-TST/resolve', async (route) => {
      const body = route.request().postDataJSON();
      resolves.push(body);
      if (body.kind === 'all_tracks') {
        await route.fulfill({ json: { status: 'not_found' } });
      } else {
        await route.continue();
      }
    });

    await openMapTab(page);
    await gesture(page);
    const frame = page.locator('#tab-5 iframe');
    const editorLink = page.locator('#tab-5 a[href^="https://kartoid.com/map/"]');
    await expect(frame).toHaveAttribute('src', /^https:\/\/kartoid\.com\/track#/, { timeout: 30000 });

    const track = resolves.find((b) => b.kind === 'track');
    expect(track, JSON.stringify(resolves)).toBeTruthy();
    expect(track.path.toLowerCase()).toMatch(/\.(gpx|kml|kmz)$/);

    const origin = new URL(page.url()).origin;
    const params = hashParams(await frame.getAttribute('src'));
    const p = params.get('p');
    expect(p.startsWith(`${origin}/cache/00001-TST/`), p).toBe(true);
    expect(decodeURIComponent(p).endsWith(track.path), p).toBe(true);
    expect(params.has('fail')).toBe(false);

    expect(hashParams(await editorLink.getAttribute('href')).get('p')).toBe(p);
    await expect(editorLink).not.toHaveAttribute('data-pending-track', 'kartoid');
  });

  for (const { status, fail } of [
    { status: 'not_prepared', fail: 'missing' },
    { status: 'error', fail: 'broken' },
  ]) {
    test(`Отказ сервера «${status}»: фрейм получает fail=${fail}, ссылка в редактор выключена`, async ({ page }) => {
      skipIfSeedMissing(test, '1-TST');
      await page.route('**/api/cache/00001-TST/resolve', (route) =>
        route.fulfill({ json: { status, message: 'e2e' } }));

      await openMapTab(page);
      await gesture(page);
      const frame = page.locator('#tab-5 iframe');
      await expect(frame).toHaveAttribute('src', /^https:\/\/kartoid\.com\/track#/, { timeout: 30000 });

      const origin = new URL(page.url()).origin;
      const params = hashParams(await frame.getAttribute('src'));
      expect(params.get('p')).toBe(`${origin}/cache/00001-TST/00001-TST-geo.zip`);
      expect(params.get('fail')).toBe(fail);
      await expect(page.locator('#tab-5 a[href^="https://kartoid.com/map/"]'))
        .toHaveAttribute('data-pending-track', 'kartoid');
    });
  }

  test('Повторное открытие вкладки не запускает подготовку заново', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    let trackResolves = 0;
    page.on('request', (req) => {
      if (req.method() !== 'POST' || !req.url().includes('/api/cache/00001-TST/resolve')) return;
      const kind = req.postDataJSON()?.kind;
      if (kind === 'all_tracks' || kind === 'track') trackResolves += 1;
    });

    await openMapTab(page);
    await gesture(page);
    const frame = page.locator('#tab-5 iframe');
    await expect(frame).toHaveAttribute('src', /^https:\/\/kartoid\.com\/track#/, { timeout: 30000 });
    const before = trackResolves;
    const src = await frame.getAttribute('src');
    expect(before).toBeGreaterThan(0);

    // Через PDF, а не через «Треки»: у вкладки треков свои resolve
    await page.locator('.tab-button[data-tab="tab-1"]').click();
    await expect(page.locator('.tab-button.active[data-tab="tab-1"]')).toBeVisible();
    await page.locator('.tab-button[data-tab="tab-5"]').click();
    await expect(page.locator('.tab-button.active[data-tab="tab-5"]')).toBeVisible();
    await page.waitForTimeout(2000);

    expect(trackResolves).toBe(before);
    await expect(frame).toHaveAttribute('src', src);
  });
});
