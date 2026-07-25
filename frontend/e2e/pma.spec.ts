import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Set setup complete so we bypass the setup page
  await page.addInitScript(() => {
    localStorage.setItem('pma_setup_complete', 'true');

    // Mock global EventSource for progressive indexing progress stream
    class MockEventSource extends EventTarget {
      url: string;
      onopen: any = null;
      onerror: any = null;
      readyState = 0;
      constructor(url: string) {
        super();
        this.url = url;
        (window as any).__mockEventSourceInstance = this;

        setTimeout(() => {
          this.readyState = 1;
          if (this.onopen) this.onopen();
          this.dispatchEvent(new Event('open'));

          // If indexing has been triggered, schedule sending progress updates
          if ((window as any).__shouldTriggerProgress) {
            (window as any).__shouldTriggerProgress = false; // Reset flag
            setTimeout(() => {
              (window as any).__triggerIndexingProgress();
            }, 100);
          }
        }, 10);
      }
      close() {
        this.readyState = 2;
      }
    }
    (window as any).EventSource = MockEventSource;

    (window as any).__triggerIndexingProgress = () => {
      const instance = (window as any).__mockEventSourceInstance;
      if (!instance) return;

      // Emit running status
      const runData = JSON.stringify({
        status: 'running',
        processed_files: 2,
        total_files: 5,
        current_file: 'file2.txt',
        progress_percent: 40,
        error: null
      });
      instance.dispatchEvent(new MessageEvent('progress', { data: runData }));

      // Emit finished status after a delay (2500ms to let Playwright assert visibility)
      setTimeout(() => {
        const doneData = JSON.stringify({
          status: 'idle',
          processed_files: 5,
          total_files: 5,
          current_file: '',
          progress_percent: 100,
          error: null
        });
        instance.dispatchEvent(new MessageEvent('progress', { data: doneData }));
      }, 2500);
    };
  });

  // Mock standard API endpoints
  await page.route('**/api/health', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        version: '0.0.70',
        status: 'ok',
        db: 'connected',
        model_ready: true,
        split_brain_sync_status: 'idle',
      }),
    });
  });

  await page.route('**/api/system/config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        gemini_model: 'gemini-2.5-flash-lite',
        ollama_model: null,
        lm_studio_model: null,
      }),
    });
  });

  await page.route('**/api/index/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'idle',
        files_indexed: 12,
        chunks_indexed: 140,
        progress_percent: 0,
        processed_files: 0,
        total_files: 0,
      }),
    });
  });

  await page.route('**/api/auth/google/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        connected: true,
        method: 'env',
      }),
    });
  });

  await page.route('**/api/query/history', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        history: [],
      }),
    });
  });

  await page.route('**/api/files/tree', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        folders: {},
      }),
    });
  });

  await page.route('**/api/llm/preferences', async (route) => {
    if (route.request().method() === 'POST') {
      const payload = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: 'Preferences saved',
          llm: payload,
        }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          provider: 'auto',
          gemini_model: 'gemini-2.5-flash-lite',
          ollama_model: null,
          lm_studio_model: null,
        }),
      });
    }
  });

  await page.route('**/api/system/info', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        volumes: [],
      }),
    });
  });

  await page.route('**/api/system/drive_info', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        volumes: [],
      }),
    });
  });

  await page.route('**/api/llm/detect', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ollama: { detected: false, models: [] },
        lm_studio: { detected: false, models: [] },
      }),
    });
  });

  await page.route('**/api/providers', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          spec: {
            id: 'gemini',
            display_name: 'Google Gemini',
            kind: 'cloud',
            default_base_url: null,
            base_url_editable: false,
            auth: 'x-goog-api-key',
            models_endpoint: '',
            models_parser: '',
            api_key_pattern: null,
            api_key_docs_url: '',
            supports_streaming: true,
            supports_tools: true,
            supports_vision: true,
            supported_features: [],
          },
          is_set: true,
          preview: 'AIzaSy...',
          stored_in: 'env',
          base_url: null,
          default_model: 'gemini-2.5-flash-lite',
          last_validation: {
            ok: true,
            latency_ms: 120,
            models: [
              { id: 'gemini-2.5-flash-lite', context_length: 1000000, pricing_hint: 0, family: 'gemini' },
              { id: 'gemini-3-pro', context_length: 1000000, pricing_hint: 0, family: 'gemini' },
            ],
            error: null,
            error_code: null,
            server_time: null,
          },
        },
      ]),
    });
  });
});

test('Search Scenario - Navigating to search, submitting query, verifying mocked search answer and sources', async ({ page }) => {
  // Mock query stream endpoint
  await page.route('**/api/query/stream', async (route) => {
    const responseData = [
      JSON.stringify({ type: 'content', text: 'Here is a mocked answer for your query.' }),
      JSON.stringify({ type: 'sources', sources: [{ file_path: 'd:/projects/folder1/doc1.txt', score: 0.9, text: 'Source content snippet.' }] }),
      JSON.stringify({ type: 'done' })
    ].join('\n');

    await route.fulfill({
      status: 200,
      contentType: 'text/plain',
      body: responseData,
    });
  });

  await page.goto('/search');

  // Find input field and fill query
  const input = page.locator('input[placeholder="Ask a follow-up or a new question..."]');
  await expect(input).toBeVisible();
  await input.fill('Hello Assistant');
  await input.press('Enter');

  // Verify answer is rendered
  const answer = page.getByText('Here is a mocked answer for your query.');
  await expect(answer).toBeVisible();

  // Verify sources are rendered
  const sourceFile = page.getByText('doc1.txt');
  await expect(sourceFile).toBeVisible();
});

test('Indexing Scenario - Entering folder path, clicking index, verifying progressive status updates show running and complete', async ({ page }) => {
  // Mock index start request
  await page.route('**/api/index/start', async (route) => {
    await page.evaluate(() => {
      (window as any).__shouldTriggerProgress = true;
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok' }),
    });
  });

  await page.goto('/library');

  // Locate folder path input
  const input = page.locator('input[placeholder="Select or drag a folder here..."]');
  await expect(input).toBeVisible();
  await input.fill('D:\\mock_folder');

  // Click Index button (exact match by role name to avoid Clear Index conflict)
  const indexBtn = page.getByRole('button', { name: 'Index', exact: true });
  await expect(indexBtn).toBeVisible();
  await indexBtn.click();

  // Verify progress status shows running state
  const progressText = page.locator('text=file2.txt (2/5)');
  await expect(progressText).toBeVisible();

  // Verify it completes successfully
  const completionMessage = page.getByText('Indexing complete — 5 files processed');
  await expect(completionMessage).toBeVisible({ timeout: 5000 });
});

test('Explorer Scenario - Tree visualization rendering by mocking files-tree, verifying folder and file tree', async ({ page }) => {
  // Intercept file tree to return specific folder structure
  await page.route('**/api/files/tree', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        folders: {
          "D:/workspace/pma_test": [
            {
              "path": "D:/workspace/pma_test/file_a.py",
              "size": 1024,
              "type": "python",
              "usage_count": 2
            },
            {
              "path": "D:/workspace/pma_test/sub/file_b.ts",
              "size": 2048,
              "type": "typescript",
              "usage_count": 0
            }
          ]
        }
      }),
    });
  });

  await page.goto('/explorer');

  // Verify root folder node is displayed (its name tagName is 'pma_test')
  const rootNode = page.locator('.select-none').getByText('pma_test');
  await expect(rootNode).toBeVisible();

  // Root folder is open by default, verify file_a.py is displayed in the tree
  const fileA = page.locator('.select-none').getByText('file_a.py');
  await expect(fileA).toBeVisible();

  // Subfolder 'sub' should also be visible but closed
  const subFolder = page.locator('.select-none').getByText('sub', { exact: true });
  await expect(subFolder).toBeVisible();

  // Click the subfolder to expand it
  await subFolder.click();

  // Verify file_b.ts is now visible in the tree
  const fileB = page.locator('.select-none').getByText('file_b.ts');
  await expect(fileB).toBeVisible();
});

test('Settings Scenario - Updating model provider and model, and verifying it is saved', async ({ page }) => {
  await page.goto('/settings');

  // Find provider select dropdown
  const providerSelect = page.locator('label:has-text("Provider") select');
  await expect(providerSelect).toBeVisible();
  
  // Select 'gemini'
  await providerSelect.selectOption('gemini');

  // Find model select dropdown
  const modelSelect = page.locator('label:has-text("Model") select');
  await expect(modelSelect).toBeVisible();
  await modelSelect.selectOption('gemini-3-pro');

  // Click Save Preference
  const saveBtn = page.locator('button', { hasText: 'Save Preference' });
  await expect(saveBtn).toBeVisible();
  
  // Verify that POST preference endpoint is successfully called with correct body on save
  const [request] = await Promise.all([
    page.waitForRequest(req => req.url().includes('/api/llm/preferences') && req.method() === 'POST'),
    saveBtn.click()
  ]);

  const postData = JSON.parse(request.postData() || '{}');
  expect(postData.provider).toBe('gemini');
  expect(postData.gemini_model).toBe('gemini-3-pro');
});
