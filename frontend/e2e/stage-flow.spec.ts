import { expect, test } from "@playwright/test";

import {
  assertMinimumTouchTargets,
  assertNoHorizontalOverflow,
} from "./support/fixtures";
import {
  assertStageHasNoPrivateFacts,
  captureState,
  createDisplayPairing,
  createRoom,
  pairStage,
} from "./support/flows";

test("stage stays public and responsive", async ({ browser }, testInfo) => {
  const host = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const stage = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
  });

  try {
    const hostPage = await host.newPage();
    const stagePage = await stage.newPage();
    const roomCode = await createRoom(hostPage);
    const pairingCode = await createDisplayPairing(hostPage);

    await pairStage(stagePage, roomCode, pairingCode);
    await expect(stagePage.getByTestId("stage-root")).toHaveAttribute(
      "data-phase",
      "day",
    );
    await assertNoHorizontalOverflow(stage);
    await assertMinimumTouchTargets(stage, 44);
    await assertStageHasNoPrivateFacts(stagePage);
    await captureState(stagePage, "stage-public", testInfo);
  } finally {
    await Promise.all([host.close(), stage.close()]);
  }
});
