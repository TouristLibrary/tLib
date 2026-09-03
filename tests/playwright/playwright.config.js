// Version 1.2 - 22.06.2026
// Описание: Конфигурация Playwright для регрессионных E2E тестов TlibWebApp. Использует BASE_URL для запуска тестов
//           против локального сервера или деплоя, и запускает Chromium в headless режиме.
// Изменения v1.1: env-gated параллелизм — при удалённом BASE_URL workers=2 и retries=2,
//           чтобы не перегружать сервер с rate limiting при параллельном прогоне.
// Изменения v1.2: isRemote учитывает IPv6-localhost (::1).

const { defineConfig } = require('@playwright/test');

const baseURL = process.env.BASE_URL || 'http://localhost:8080';

// При удалённом сервере снижаем параллелизм и увеличиваем ретраи:
// сервер работает с rate limiting (инвариант проекта), и 4 параллельных воркера
// вызывают 429/таймауты на кнопке и справочниках.
const isRemote = !!process.env.BASE_URL && !/localhost|127\.0\.0\.1|\[?::1\]?/.test(process.env.BASE_URL);

module.exports = defineConfig({
  globalSetup: require.resolve('./global-setup.js'),
  testDir: '../e2e',
  timeout: 120_000,
  expect: {
    timeout: 15_000,
  },
  retries: isRemote ? 2 : 1,
  workers: isRemote ? 2 : undefined,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    headless: true,
    viewport: { width: 1261, height: 885 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
});

