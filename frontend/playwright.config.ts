import { randomUUID } from "node:crypto";
import { resolve } from "node:path";

import { defineConfig } from "@playwright/test";

const controlToken = process.env.E2E_CONTROL_TOKEN ?? randomUUID();
process.env.E2E_CONTROL_TOKEN = controlToken;
const pythonExecutable = resolve(
  process.cwd(),
  process.env.E2E_PYTHON ??
    (process.platform === "win32"
      ? "../backend/.venv/Scripts/python.exe"
      : "../backend/.venv/bin/python"),
);

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 120_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    browserName: "chromium",
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "pnpm dev --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `"${pythonExecutable}" e2e/support/test_server.py`,
      url: "http://127.0.0.1:8000/healthz",
      env: {
        E2E_CONTROL_TOKEN: controlToken,
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
