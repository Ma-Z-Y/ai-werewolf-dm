import {
  expect,
  type APIRequestContext,
  type BrowserContext,
} from "@playwright/test";

const controlToken = process.env.E2E_CONTROL_TOKEN;
if (!controlToken) {
  throw new Error("E2E_CONTROL_TOKEN is not set");
}

export const controlHeaders = {
  "X-Test-Control": controlToken,
};

export async function advanceRoom(
  request: APIRequestContext,
  roomCode: string,
  seconds: number,
): Promise<void> {
  const response = await request.post(
    `http://127.0.0.1:8000/__test__/rooms/${roomCode}/advance`,
    {
      headers: controlHeaders,
      data: { seconds },
    },
  );
  if (!response.ok()) {
    throw new Error(
      `advanceRoom failed: ${response.status()} ${await response.text()}`,
    );
  }
}

export async function assertNoHorizontalOverflow(
  context: BrowserContext,
): Promise<void> {
  const page = context.pages()[0];
  if (!page) {
    throw new Error("browser context has no page");
  }
  await page.locator("html").waitFor();
  const overflows = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflows).toBe(false);
}

export async function assertMinimumTouchTargets(
  context: BrowserContext,
  minimum: number,
): Promise<void> {
  const page = context.pages()[0];
  if (!page) {
    throw new Error("browser context has no page");
  }
  const controls = page.locator(
    'button:visible, a:visible, input:visible, textarea:visible, select:visible, [role="button"]:visible',
  );
  for (const control of await controls.all()) {
    const box = await control.boundingBox();
    if (!box) continue;
    expect(box.width).toBeGreaterThanOrEqual(minimum);
    expect(box.height).toBeGreaterThanOrEqual(minimum);
  }
}

export async function assertPlayerLayout(
  context: BrowserContext,
): Promise<void> {
  await assertNoHorizontalOverflow(context);
  await assertMinimumTouchTargets(context, 44);
}
