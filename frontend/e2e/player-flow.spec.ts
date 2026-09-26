import { expect, test } from "@playwright/test";

import {
  assertMinimumTouchTargets,
  assertNoHorizontalOverflow,
  assertPlayerLayout,
  advanceRoom,
} from "./support/fixtures";
import {
  assertStageHasNoPrivateFacts,
  advanceDiscussionByTimeout,
  captureState,
  confirmAllRoles,
  createDisplayPairing,
  createRoom,
  joinAndReady,
  pairStage,
  passDiscussion,
  playNight,
  submitNormalVotes,
  submitPkVotes,
  waitForGameEnd,
  waitForNight,
} from "./support/flows";

test("six players host and stage complete a deterministic game", async ({
  browser,
  request,
}, testInfo) => {
  const host = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const stage = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
  });
  const players = await Promise.all(
    Array.from({ length: 6 }, (_, index) =>
      browser.newContext({
        viewport:
          index === 0
            ? { width: 320, height: 568 }
            : { width: 375, height: 667 },
      }),
    ),
  );

  try {
    const hostPage = await host.newPage();
    const stagePage = await stage.newPage();
    const roomCode = await createRoom(hostPage);

    await assertNoHorizontalOverflow(host);
    await assertMinimumTouchTargets(host, 44);
    await captureState(hostPage, "host-created", testInfo);

    const pairingCode = await createDisplayPairing(hostPage);
    for (const [index, context] of players.entries()) {
      const page = await context.newPage();
      await joinAndReady(page, index + 1, roomCode);
    }
    for (const context of players) {
      await assertPlayerLayout(context);
    }
    await captureState(players[0].pages()[0], "player-lobby", testInfo);

    await confirmAllRoles(players, testInfo);
    await pairStage(stagePage, roomCode, pairingCode);
    await assertPlayerLayout(players[0]);
    await assertNoHorizontalOverflow(stage);
    await assertMinimumTouchTargets(stage, 44);
    await assertStageHasNoPrivateFacts(stagePage);
    await captureState(stagePage, "stage-paired", testInfo);

    await playNight(players, 2, {
      seerTarget: 1,
      witchAction: "antidote",
    });
    await assertStageHasNoPrivateFacts(stagePage);
    await advanceRoom(request, roomCode, 45);
    await passDiscussion(players);
    await submitNormalVotes(players, {
      1: 4,
      2: 4,
      3: 4,
      4: 1,
      5: 1,
      6: 1,
    }, "h2:text-is('PK 讨论')");
    await passDiscussion(players, "PK 讨论");
    await expect(players[0].pages()[0].getByTestId("pk-phase")).toBeVisible();
    await submitPkVotes(players, {
      2: 1,
      3: 1,
      5: 4,
      6: 4,
    }, "[data-action='WOLF_NOMINATE_KILL']:visible");
    await assertStageHasNoPrivateFacts(stagePage);

    await hostPage.getByRole("button", { name: "暂停游戏" }).click();
    await expect(hostPage.getByRole("button", { name: "恢复游戏" })).toBeVisible();
    await expect(stagePage.getByTestId("stage-pause-banner")).toBeVisible();
    await advanceRoom(request, roomCode, 10);
    await assertPlayerLayout(players[0]);
    await captureState(players[0].pages()[0], "player-paused", testInfo);

    await hostPage.getByRole("button", { name: "恢复游戏" }).click();
    await expect(hostPage.getByRole("button", { name: "暂停游戏" })).toBeVisible();
    await players[1].pages()[0].reload();
    await expect(players[1].pages()[0].getByRole("heading", { name: "玩家席" }))
      .toBeVisible();
    await expect(players[1].pages()[0].getByText("已连接")).toBeVisible();
    await assertPlayerLayout(players[1]);
    await captureState(
      players[1].pages()[0],
      "player-reconnected",
      testInfo,
    );

    await waitForNight(players);
    await playNight(players, 2, {
      seerTarget: 1,
      witchAction: "skip",
    });
    await assertStageHasNoPrivateFacts(stagePage);
    await advanceDiscussionByTimeout(players, request, roomCode);
    await passDiscussion(players);
    await submitNormalVotes(players, {
      1: 4,
      3: 4,
      4: 1,
      5: 4,
      6: 4,
    }, "h2:text-is('终局结果')");
    await waitForGameEnd(players);

    await expect(stagePage.getByTestId("stage-result")).toBeVisible();
    await assertStageHasNoPrivateFacts(stagePage);
    await captureState(stagePage, "stage-game-end", testInfo);

    await assertPlayerLayout(players[0]);
    const firstPlayerPage = players[0].pages()[0];
    await firstPlayerPage.getByRole("link", { name: "查看玩家回放" }).click();
    await expect(firstPlayerPage).toHaveURL(
      new RegExp(`/replay/${roomCode}\\?revision=\\d+$`),
    );
    expect(firstPlayerPage.url()).not.toContain("token");
    await expect(firstPlayerPage.getByTestId("replay-public-timeline")).toBeVisible();
    await expect(
      firstPlayerPage.getByRole("heading", { name: "玩家回放" }),
    ).toBeVisible();
    await captureState(firstPlayerPage, "player-replay", testInfo);
  } finally {
    await Promise.all([
      host.close(),
      stage.close(),
      ...players.map((context) => context.close()),
    ]);
  }
});
