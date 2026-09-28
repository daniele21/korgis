const { test, expect } = require('@playwright/test');

async function openStudio(page) {
  await page.route('https://fonts.googleapis.com/**', (route) => route.abort());
  await page.route('https://fonts.gstatic.com/**', (route) => route.abort());
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('link', { name: 'Playground' })).toBeVisible();
  await expect(page).toHaveURL(/\/overview$/);
}

async function openPlayground(page) {
  await openStudio(page);
  await page.getByRole('link', { name: 'Playground' }).click();
  await expect(page).toHaveURL(/\/playground$/);
  await expect(page.locator('#chat-tab')).toBeVisible();
  await expect(page.locator('[data-playground-task="chat"]')).toBeVisible();
  await expect(page.locator('[data-select-task-model="e2e-switchable"]')).toBeVisible();
}

async function submitMessage(page, text) {
  const requestPromise = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'POST' && url.pathname === '/v1/chat/completions';
  });
  await page.locator('#chat-textarea').fill(text);
  await page.locator('#send-chat-btn').click();
  return requestPromise;
}

test('public runtime contract exposes coherent inference identity and status surfaces', async ({ request }) => {
  const modelsResponse = await request.get('/v1/models');
  expect(modelsResponse.ok()).toBeTruthy();
  const models = await modelsResponse.json();
  expect(models.data.map((item) => item.key || item.id)).toEqual(
    expect.arrayContaining(['e2e-switchable', 'e2e-alt'])
  );

  const identityResponse = await request.get('/v1/runtime/identity');
  expect(identityResponse.ok()).toBeTruthy();
  const identity = await identityResponse.json();
  expect(identity.protocol_version).toBe('local-llm-identity-v1');
  expect(identity.default_model).toBe('e2e-switchable');
  expect(Object.keys(identity.models)).toEqual(
    expect.arrayContaining(['e2e-switchable', 'e2e-alt'])
  );
  expect(JSON.stringify(identity)).not.toContain('/e2e/');

  const statusResponse = await request.get('/status');
  expect(statusResponse.ok()).toBeTruthy();
  const status = await statusResponse.json();
  expect(status.default_model).toBe('e2e-switchable');
  expect(Object.keys(status.models)).toEqual(
    expect.arrayContaining(['e2e-switchable', 'e2e-alt'])
  );
});

test('overview prioritizes readiness, residency, budget, workload and capacity', async ({ page }) => {
  await openStudio(page);

  const strip = page.locator('.overview-readiness-strip');
  await expect(strip).toBeVisible();
  for (const label of ['Readiness', 'Resident', 'AI budget', 'Workload', 'Capacity']) {
    await expect(strip.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(page.getByRole('heading', { name: 'What can run next?' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What is using the runtime?' })).toBeVisible();
  const evidenceDetails = page.locator('.overview-evidence-details');
  await expect(evidenceDetails.getByText('Runtime evidence & provenance', { exact: true })).toBeVisible();
  await expect(evidenceDetails.getByText('Runtime fingerprint', { exact: true })).toBeHidden();
});

test('models and runtimes owns the lifecycle, resource recovery and deep-linked detail surface @full-media', async ({ page, request }) => {
  await openStudio(page);
  await page.getByRole('link', { name: 'Models & Runtimes' }).click();
  await expect(page).toHaveURL(/\/models$/);

  await expect(page.getByRole('heading', { name: 'Memory & Residency' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Model inventory' })).toBeVisible();
  const inventoryHeaders = page.locator('.control-plane-models__table thead th');
  await expect(inventoryHeaders.filter({ hasText: /^Artifact$/ })).toBeVisible();
  await expect(inventoryHeaders.filter({ hasText: /^Runtime$/ })).toBeVisible();
  await expect(inventoryHeaders.filter({ hasText: /^Route$/ })).toBeVisible();
  await expect(inventoryHeaders.filter({ hasText: /^Memory evidence$/ })).toBeVisible();
  await expect(page.getByText('Open lifecycle controls')).toHaveCount(0);

  await page.locator('[data-set-default-model="e2e-alt"]').first().click();
  await expect.poll(async () => {
    const response = await request.get('/status');
    const payload = await response.json();
    return payload.default_model;
  }).toBe('e2e-alt');

  await page.locator('[data-set-default-model="e2e-switchable"]').first().click();
  await expect.poll(async () => {
    const response = await request.get('/status');
    const payload = await response.json();
    return payload.default_model;
  }).toBe('e2e-switchable');

  await page.locator('[data-open-model="e2e-alt"]').first().click();
  await expect(page).toHaveURL(/\/models\/[^/]+$/);
  await expect(page.locator('[data-model-detail]')).toContainText('e2e-alt');

  const detailUrl = page.url();
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(detailUrl);
  await expect(page.locator('[data-model-detail]')).toContainText('e2e-alt');
});

test('playground is task-first and structured mode owns JSON output', async ({ page }) => {
  await openPlayground(page);

  await expect(page.locator('[data-playground-task="chat"]')).toHaveAttribute('aria-selected', 'true');
  await page.locator('[data-playground-task="structured-output"]').click();
  await expect(page.locator('[data-playground-task="structured-output"]')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#param-force-json')).toBeChecked();

  const request = await submitMessage(page, 'Return the answer as structured JSON.');
  const payload = request.postDataJSON();
  expect(payload.response_format?.type).toBe('json_object');

  await expect(page.locator('#chat-messages-container')).toContainText('"answer":42');
});

test('playground routes an explicit request to the task-selected resident model', async ({ page }) => {
  await openPlayground(page);

  const modelSelect = page.locator('#model-select');
  await page.locator('[data-select-task-model="e2e-alt"]').click();
  await expect(modelSelect).toHaveValue('e2e-alt');

  const request = await submitMessage(page, 'Route this request to the alternate runtime.');
  const payload = request.postDataJSON();
  expect(payload.model).toBe('e2e-alt');

  await expect(page.locator('#typing-status')).toBeHidden();
  await expect(page.locator('#chat-messages-container')).toContainText('84');
});

test('runtime status polling becomes visible while a request is genuinely generating', async ({ page }) => {
  await openPlayground(page);

  await submitMessage(page, 'Observe this request while it is running. [slow-status]');

  await expect(page.locator('#typing-text')).toContainText('Generazione in corso', {
    timeout: 5_000,
  });
  await expect(page.locator('#chat-messages-container')).toContainText('42', {
    timeout: 10_000,
  });
  await expect(page.locator('#typing-status')).toBeHidden();
});

test('a failed inference does not poison the next request @full-media', async ({ page }) => {
  await openPlayground(page);

  let intercepted = false;
  await page.route('**/v1/chat/completions', async (route) => {
    if (!intercepted) {
      intercepted = true;
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: {
            code: 'backend_unavailable',
            message: 'Fixture backend is temporarily unavailable.',
            retryable: true,
            details: {},
          },
        }),
      });
      return;
    }
    await route.continue();
  });

  await page.locator('#chat-textarea').fill('Fail this request once.');
  await page.locator('#send-chat-btn').click();
  await expect(page.locator('#chat-messages-container')).toContainText(
    'Fixture backend is temporarily unavailable.'
  );
  await expect(page.locator('#send-chat-btn')).toBeEnabled();

  await page.locator('#chat-textarea').fill('Now recover.');
  await page.locator('#send-chat-btn').click();
  await expect(page.locator('#chat-messages-container')).toContainText('42');
  await expect(page.locator('#typing-status')).toBeHidden();
  await expect(page.locator('#send-chat-btn')).toBeEnabled();
});
