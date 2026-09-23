import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: '.',
  testMatch: 'browser.spec.mjs',
  outputDir: '../../.architecture-review-tests',
  use: { screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
