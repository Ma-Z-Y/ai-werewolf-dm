import {
  expect,
  type APIRequestContext,
  type BrowserContext,
  type Page,
  type TestInfo,
} from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";

import { advanceRoom, assertPlayerLayout } from "./fixtures";

type VoteTargets = Record<number, number>;
type NightWitchAction = "antidote" | "skip";

function pageFor(context: BrowserContext): Page {
  const page = context.pages()[0];
  if (!page) {
    throw new Error("browser context has no page");
  }
  return page;
}

async function isVisible(page: Page, selector: string): Promise<boolean> {
  const locator = page.locator(selector);
  return (await locator.count()) > 0 && locator.first().isVisible();
}

async function hasHeading(page: Page, name: string): Promise<boolean> {
  return page
    .getByRole("heading", { name })
    .isVisible()
    .catch(() => false);
}

async function anyVisible(pages: Page[], selector: string): Promise<boolean> {
  const visible = await Promise.all(
    pages.map((page) => isVisible(page, selector)),
  );
  return visible.some(Boolean);
}

async function discussionSignature(pages: Page[]): Promise<string> {
  const visible = await Promise.all(
    pages.map((page) =>
      isVisible(page, "[data-testid='speech-action']:visible"),
    ),
  );
  return visible.map(String).join(",");
}

async function driveUntilVisible(
  pages: Page[],
  nextSelector: string,
  action: () => Promise<void>,
): Promise<void> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (await anyVisible(pages, nextSelector)) {
      return;
    }
    await action().catch(() => undefined);
    await pages[0].waitForTimeout(50);
  }
  await expect(pages[0].locator(nextSelector)).toBeVisible();
}

export async function captureState(
  page: Page,
  label: string,
  testInfo?: TestInfo,
): Promise<void> {
  const path =
    testInfo === undefined
      ? join(
          process.cwd(),
          "test-results",
          "states",
          `${label}-${Date.now()}.png`,
        )
      : testInfo.outputPath(`${label}.png`);
  await mkdir(dirname(path), { recursive: true });
  await page.screenshot({ path, fullPage: true });
}

export async function assertStageHasNoPrivateFacts(page: Page): Promise<void> {
  for (const secret of [
    "WEREWOLF",
    "WITCH_POTIONS",
    "SEER_CHECK",
    "狼人队友",
    "今夜狼人袭击",
    "狼队决定袭击",
    "解药可用",
    "毒药可用",
  ]) {
    await expect(page.locator("body")).not.toContainText(secret);
  }
  await expect(page.locator("body")).not.toContainText(
    /预言家查验 \d+ 号为/,
  );
  await expect(
    page.getByRole("button", { name: "按住查看身份" }),
  ).not.toBeVisible();
}

export async function createRoom(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByLabel("主持人名字").fill("主持人");
  await Promise.all([
    page.waitForURL(/\/host\/[A-Z0-9]+$/),
    page.getByRole("button", { name: "创建房间" }).click(),
  ]);
  await expect(
    page.getByRole("button", { name: "生成共享屏配对码" }),
  ).toBeVisible();
  const match = new URL(page.url()).pathname.match(/\/host\/([^/]+)$/);
  if (!match) {
    throw new Error(`host room URL is invalid: ${page.url()}`);
  }
  return decodeURIComponent(match[1]);
}

export async function createDisplayPairing(page: Page): Promise<string> {
  await page
    .getByRole("button", { name: "生成共享屏配对码" })
    .click();
  const pairingCode = page.locator("strong").filter({
    hasText: /^\d{6}$/,
  });
  await expect(pairingCode).toBeVisible();
  const code = await pairingCode.textContent();
  if (code === null) {
    throw new Error("display pairing code is missing");
  }
  return code;
}

export async function joinAndReady(
  page: Page,
  seat: number,
  roomCode: string,
): Promise<void> {
  await page.goto(`/join/${roomCode}`);
  await expect(page.getByLabel("房间码")).toHaveValue(roomCode);
  await page.getByLabel("名字").fill(`${seat}号`);
  await page.getByRole("button", { name: "加入房间" }).click();
  await expect(page.getByRole("heading", { name: "玩家大厅" })).toBeVisible();
  const readyButton = page.getByRole("button", { name: "准备" });
  await expect(readyButton).toBeVisible();
  await readyButton.click({ force: true, timeout: 2_000 });
  await expect
    .poll(async () => {
      if (
        await page
          .getByRole("button", { name: "取消准备" })
          .isVisible()
          .catch(() => false)
      ) {
        return true;
      }
      return page
        .getByRole("heading", { name: "角色揭示" })
        .isVisible()
        .catch(() => false);
    })
    .toBe(true);
  await assertPlayerLayout(page.context());
  if (
    await page
      .getByRole("heading", { name: "玩家大厅" })
      .isVisible()
      .catch(() => false)
  ) {
    await captureState(page, `player-${seat}-lobby`);
  }
}

export async function confirmAllRoles(
  contexts: BrowserContext[],
  testInfo?: TestInfo,
): Promise<void> {
  for (const context of contexts) {
    const page = pageFor(context);
    const reveal = page.getByRole("button", { name: "按住查看身份" });
    await expect(reveal).toBeVisible();
    await reveal.focus();
    await page.keyboard.down("Enter");
    await expect(reveal).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.up("Enter");
    await expect(reveal).toHaveAttribute("aria-expanded", "false");

    await page
      .getByRole("button", { name: "确认身份" })
      .click({ force: true, timeout: 2_000 });
    await expect(
      page.locator("button").filter({ hasText: /确认身份|处理中|已确认/ }),
    ).toHaveCount(0);
    await assertPlayerLayout(context);
    if (testInfo !== undefined) {
      await captureState(page, "role-confirmed", testInfo);
    }
  }
}

export async function pairStage(
  page: Page,
  roomCode: string,
  pairingCode: string,
): Promise<void> {
  await page.goto(`/stage/${roomCode}`);
  await page.getByLabel("配对码").fill(pairingCode);
  await page.getByRole("button", { name: "连接共享屏" }).click();
  await expect(page.getByTestId("stage-root")).toBeVisible();
}

export async function waitForNight(
  contexts: BrowserContext[],
): Promise<void> {
  await expect
    .poll(async () => {
      for (const context of contexts) {
        if (
          await isVisible(
            pageFor(context),
            "[data-action='WOLF_NOMINATE_KILL']:visible",
          )
        ) {
          return true;
        }
      }
      return false;
    })
    .toBe(true);
}

export async function playNight(
  contexts: BrowserContext[],
  targetSeat: number,
  options: {
    seerTarget: number;
    witchAction: NightWitchAction;
  },
): Promise<void> {
  const pages = contexts.map(pageFor);

  const seerPage = pages[2];
  const witchPage = pages[0];
  await driveUntilVisible(
    pages,
    "[data-action='SEER_INSPECT']:visible",
    async () => {
      for (const wolfPage of [pages[4], pages[5]]) {
        const wolfAction = wolfPage.locator(
          "[data-action='WOLF_NOMINATE_KILL']:visible",
        );
        if (!(await wolfAction.isVisible().catch(() => false))) {
          continue;
        }
        const target = wolfPage.getByTestId(`night-target-${targetSeat}`);
        if ((await target.count()) === 0) {
          continue;
        }
        if (
          (await target.getAttribute("aria-pressed", { timeout: 1_000 })) !==
          "true"
        ) {
          await target.click({ force: true, timeout: 1_000 });
        }
        const confirm = wolfPage.getByRole("button", { name: "确认行动" });
        if (
          (await confirm.count()) > 0 &&
          (await confirm.isEnabled({ timeout: 1_000 }))
        ) {
          await confirm.click({ force: true, timeout: 1_000 });
        }
      }
    },
  );

  const witchActionSelector =
    options.witchAction === "antidote"
      ? "[data-action='WITCH_USE_ANTIDOTE']:visible"
      : "[data-action='WITCH_SKIP']:visible";
  await driveUntilVisible(pages, witchActionSelector, async () => {
    const seerAction = seerPage.locator(
      "[data-action='SEER_INSPECT']:visible",
    );
    if (!(await seerAction.isVisible().catch(() => false))) {
      return;
    }
    const target = seerPage.getByTestId(
      `night-target-${options.seerTarget}`,
    );
    if ((await target.count()) === 0) {
      return;
    }
    if (
      (await target.getAttribute("aria-pressed", { timeout: 1_000 })) !==
      "true"
    ) {
      await target.click({ force: true, timeout: 1_000 });
    }
    const confirm = seerPage.getByRole("button", { name: "确认行动" });
    if (
      (await confirm.count()) > 0 &&
      (await confirm.isEnabled({ timeout: 1_000 }))
    ) {
      await confirm.click({ force: true, timeout: 1_000 });
    }
  });

  await driveUntilVisible(
    pages,
    "h2:text-is('白天讨论')",
    async () => {
      const witchAction = witchPage.locator(witchActionSelector);
      if (!(await witchAction.isVisible().catch(() => false))) {
        return;
      }
      await witchAction.click({ force: true, timeout: 1_000 });
      const confirm = witchPage.getByRole("button", { name: "确认行动" });
      if (
        (await confirm.count()) > 0 &&
        (await confirm.isEnabled({ timeout: 1_000 }))
      ) {
        await confirm.click({ force: true, timeout: 1_000 });
      }
    },
  );
}

export async function passDiscussion(
  contexts: BrowserContext[],
  expectedPhase: "白天讨论" | "PK 讨论" = "白天讨论",
): Promise<void> {
  let discussionStarted = false;
  for (let safety = 0; safety < 20; safety += 1) {
    const pages = contexts.map(pageFor);
    const dayVoteVisible = await Promise.all(
      pages.map((page) => hasHeading(page, "白天投票")),
    );
    const pkVoteVisible = await Promise.all(
      pages.map((page) => hasHeading(page, "PK 投票")),
    );
    if (
      pkVoteVisible.some(Boolean) ||
      (expectedPhase === "白天讨论" && dayVoteVisible.some(Boolean))
    ) {
      return;
    }

    if (!discussionStarted) {
      const discussionVisible = await Promise.all(
        pages.map((page) => hasHeading(page, expectedPhase)),
      );
      if (discussionVisible.some(Boolean)) {
        discussionStarted = true;
      } else {
        await expect
          .poll(async () =>
            Promise.all(pages.map((page) => hasHeading(page, expectedPhase))),
          )
          .toContain(true);
        continue;
      }
    }

    let speaker: Page | undefined;
    for (const page of pages) {
      if (await isVisible(page, "[data-testid='speech-action']:visible")) {
        speaker = page;
        break;
      }
    }
    if (speaker) {
      await expect(
        speaker.locator("[data-testid='speech-action']:visible"),
      ).toBeVisible();
      const pass = speaker.getByRole("button", { name: "跳过发言" });
      await pass.click({ force: true, timeout: 1_000 });
      await expect(pass).toBeHidden();
      continue;
    }

    await expect
      .poll(async () => {
        for (const page of pages) {
          if (
            await isVisible(page, "[data-testid='speech-action']:visible")
          ) {
            return true;
          }
          if (
            (await hasHeading(page, "白天投票")) ||
            (await hasHeading(page, "PK 投票"))
          ) {
            return true;
          }
        }
        return false;
      })
      .toBe(true);
  }
  throw new Error("discussion did not reach a vote");
}

export async function advanceDiscussionByTimeout(
  contexts: BrowserContext[],
  request: APIRequestContext,
  roomCode: string,
): Promise<void> {
  const pages = contexts.map(pageFor);
  let previousSignature = await discussionSignature(pages);
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    const voteVisible = await Promise.all(
      pages.map(async (page) => {
        return (
          (await hasHeading(page, "白天投票")) ||
          (await hasHeading(page, "PK 投票"))
        );
      }),
    );
    if (voteVisible.some(Boolean)) {
      return;
    }
    await advanceRoom(request, roomCode, 45);
    await expect
      .poll(async () => {
        const vote = await Promise.all(
          pages.map(async (page) => {
            return (
              (await hasHeading(page, "白天投票")) ||
              (await hasHeading(page, "PK 投票"))
            );
          }),
        );
        const currentSignature = await discussionSignature(pages);
        return vote.some(Boolean) || currentSignature !== previousSignature;
      })
      .toBe(true);
    previousSignature = await discussionSignature(pages);
  }
  throw new Error("discussion timeout did not reach a vote");
}

async function submitVotes(
  contexts: BrowserContext[],
  votes: VoteTargets,
  phase: "白天投票" | "PK 投票",
  nextSelector: string,
): Promise<void> {
  const pages = contexts.map(pageFor);
  await driveUntilVisible(pages, nextSelector, async () => {
    for (const [seat, target] of Object.entries(votes)) {
      const page = pageFor(contexts[Number(seat) - 1]);
      if (!(await hasHeading(page, phase))) {
        continue;
      }
      const targetButton = page.getByTestId(`vote-target-${target}`);
      if ((await targetButton.count()) === 0) {
        continue;
      }
      if (
        (await targetButton.getAttribute("aria-pressed", {
          timeout: 1_000,
        })) !== "true"
      ) {
        await targetButton.click({ force: true, timeout: 1_000 });
      }
      const confirm = page.getByRole("button", { name: "确认投票" });
      if (
        (await confirm.count()) > 0 &&
        (await confirm.isEnabled({ timeout: 1_000 }))
      ) {
        await confirm.click({ force: true, timeout: 1_000 });
      }
    }
  });
}

export async function submitNormalVotes(
  contexts: BrowserContext[],
  votes: VoteTargets,
  nextSelector: string,
): Promise<void> {
  await submitVotes(contexts, votes, "白天投票", nextSelector);
}

export async function submitPkVotes(
  contexts: BrowserContext[],
  votes: VoteTargets,
  nextSelector: string,
): Promise<void> {
  await submitVotes(contexts, votes, "PK 投票", nextSelector);
}

export async function waitForGameEnd(
  contexts: BrowserContext[],
): Promise<void> {
  await expect
    .poll(async () => {
      for (const context of contexts) {
        if (
          await pageFor(context)
            .getByText("终局结果")
            .isVisible()
            .catch(() => false)
        ) {
          return true;
        }
      }
      return false;
    })
    .toBe(true);
}
