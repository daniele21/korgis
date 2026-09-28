const { defineConfig } = require('@playwright/test');

const isCI = Boolean(process.env.CI);

module.exports = defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.js',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  outputDir: 'test-results',
  reporter: isCI
    ? [['line'], ['json', { outputFile: 'test-results/playwright-results.json' }]]
    : [['list'], ['json', { outputFile: 'test-results/playwright-results.json' }]],
  use: {
    baseURL: 'http://127.0.0.1:8765',
    browserName: 'chromium',
    trace: isCI ? 'off' : 'retain-on-failure',
    screenshot: 'on',
    video: 'off',
  },
  projects: [
    {
      name: 'assertions-and-screenshots',
      grepInvert: /@full-media/i,
    },
    {
      name: 'full-media',
      grep: /@full-media/i,
      use: { video: 'on' },
    },
  ],
  webServer: {
    command: 'exec python tests/e2e/fixture_runner.py',
    url: 'http://127.0.0.1:8765/health',
    reuseExistingServer: false,
    timeout: 30_000,
    gracefulShutdown: {
      signal: 'SIGTERM',
      timeout: 8_000,
    },
  },
});
