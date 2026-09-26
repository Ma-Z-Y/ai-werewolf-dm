import { expect, test } from "@playwright/test";

import {
  assertMinimumTouchTargets,
  assertNoHorizontalOverflow,
} from "./support/fixtures";
import {
  captureState,
  createDisplayPairing,
  createRoom,
} from "./support/flows";

test("host controls pair and pause on mobile", async ({ browser }, testInfo) => {
  const host = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });

  try {
    const page = await host.newPage();
    await createRoom(page);
    const pairingCode = await createDisplayPairing(page);
    expect(pairingCode).toMatch(/^\d{6}$/);

    await assertNoHorizontalOverflow(host);
    await assertMinimumTouchTargets(host, 44);
    await page.getByRole("button", { name: "暂停游戏" }).click();
    await expect(page.getByRole("button", { name: "恢复游戏" })).toBeVisible();
    await page.getByRole("button", { name: "恢复游戏" }).click();
    await expect(page.getByRole("button", { name: "暂停游戏" })).toBeVisible();

    await captureState(page, "host-controls", testInfo);
  } finally {
    await host.close();
  }
});
