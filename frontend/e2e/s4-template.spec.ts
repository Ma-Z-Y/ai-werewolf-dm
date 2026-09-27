import {
  expect,
  test,
  type APIRequestContext,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";

import { parseServerMessage } from "../src/protocol/parseServerMessage";

import { advanceRoom, controlHeaders } from "./support/fixtures";
import {
  advanceDiscussionByTimeout,
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

const publicDm = {
  type: "dm.message",
  server_time: "2099-01-01T00:00:00.000Z",
  outbox_seq: 7,
  message: {
    message_id: "00000000-0000-0000-0000-000000000001",
    room_id: "00000000-0000-0000-0000-000000000002",
    revision: 6,
    channel: "public",
    audience_bindings: [],
    text: "当前阶段：夜晚开始，第 0 天。",
    source: "template",
  },
};

const validSeatDm = {
  ...publicDm,
  message: {
    ...publicDm.message,
    channel: "seat",
    audience_bindings: [
      {
        seat_id: 1,
        session_id: "00000000-0000-0000-0000-000000000003",
      },
    ],
  },
};

interface FlowCapture {
  roomCode: string;
  host: Page;
  stage: Page;
  players: Page[];
  playerContexts: BrowserContext[];
  dmMessages: unknown[];
  messagesByPage: unknown[][];
  pages: Page[];
  cleanup: () => Promise<void>;
}

interface DeterministicRun {
  roomCode: string;
  dmMessages: unknown[];
  playerPublicMessages: unknown[][];
  cleanup: () => Promise<void>;
}

interface RecoveryRun {
  dmMessagesByPage: unknown[][];
  pauseBoundary: number;
  reconnectBoundary: number;
  paused: boolean;
  reconnected: boolean;
  cleanup: () => Promise<void>;
}

function captureDmMessages(page: Page, ...sinks: unknown[][]): void {
  page.on("websocket", (socket) => {
    socket.on("framereceived", (event) => {
      const raw =
        typeof event.payload === "string"
          ? event.payload
          : Buffer.isBuffer(event.payload)
            ? event.payload.toString("utf8")
            : null;
      if (raw === null) return;
      let payload: unknown;
      try {
        payload = JSON.parse(raw);
      } catch {
        return;
      }
      if (
        typeof payload === "object" &&
        payload !== null &&
        (payload as { type?: unknown }).type === "dm.message"
      ) {
        for (const sink of sinks) sink.push(payload);
      }
    });
  });
}

async function installDeterministicCommandIds(
  context: BrowserContext,
  prefix: string,
): Promise<void> {
  await context.addInitScript(
    ({ prefix: browserPrefix }) => {
      const sequenceKey = `__codex_e2e_command_sequence__:${browserPrefix}`;
      const readSequence = () => {
        try {
          return Number(globalThis.sessionStorage.getItem(sequenceKey) ?? "0");
        } catch {
          return 0;
        }
      };
      const writeSequence = (value: number) => {
        try {
          globalThis.sessionStorage.setItem(sequenceKey, String(value));
        } catch {
          // The deterministic E2E page has same-origin storage in practice.
        }
      };
      const hash32 = (value: string, seed: number) => {
        let hash = seed >>> 0;
        for (const character of value) {
          hash ^= character.charCodeAt(0);
          hash = Math.imul(hash, 0x01000193) >>> 0;
        }
        return hash;
      };
      Object.defineProperty(globalThis.crypto, "randomUUID", {
        configurable: true,
        value: () => {
          const sequence = readSequence() + 1;
          writeSequence(sequence);
          const suffix = sequence
            .toString(16)
            .padStart(12, "0")
            .slice(-12);
          const first = hash32(browserPrefix, 0x811c9dc5)
            .toString(16)
            .padStart(8, "0");
          const second = hash32(browserPrefix, 0x9e3779b9)
            .toString(16)
            .padStart(8, "0")
            .slice(0, 4);
          return `${first}-${second}-4000-8000-${suffix}`;
        },
      });
    },
    { prefix },
  );
}

const activeCleanups = new Set<() => Promise<void>>();

function registerCleanup(cleanup: () => Promise<void>) {
  let closed = false;
  const wrapped = async () => {
    if (closed) return;
    closed = true;
    activeCleanups.delete(wrapped);
    await cleanup();
  };
  activeCleanups.add(wrapped);
  return wrapped;
}

test.afterEach(async () => {
  await Promise.all([...activeCleanups].map((cleanup) => cleanup()));
});

async function completeTemplateOnlyFlow(options: {
  browser: Browser;
  request: APIRequestContext;
  onContext?: (context: BrowserContext) => Promise<void>;
  deterministicCommandIds?: boolean;
  witchAction?: "antidote" | "skip";
}): Promise<FlowCapture> {
  const contexts: BrowserContext[] = [];
  const dmMessages: unknown[] = [];
  const messagesByPage: unknown[][] = [];
  const createContext = async (
    viewport: { width: number; height: number },
    prefix: string,
  ) => {
    const context = await options.browser.newContext({
      serviceWorkers: "block",
      viewport,
    });
    contexts.push(context);
    if (options.deterministicCommandIds === true) {
      await installDeterministicCommandIds(context, prefix);
    }
    await options.onContext?.(context);
    return context;
  };

  const hostContext = await createContext(
    { width: 390, height: 844 },
    "host",
  );
  const stageContext = await createContext(
    { width: 1920, height: 1080 },
    "stage",
  );
  const playerContexts = await Promise.all(
    Array.from({ length: 6 }, (_, index) =>
      createContext(
        index === 0
          ? { width: 320, height: 568 }
          : { width: 375, height: 667 },
        `seat-${index + 1}`,
      ),
    ),
  );

  const host = await hostContext.newPage();
  const stage = await stageContext.newPage();
  const players = await Promise.all(
    playerContexts.map((context) => context.newPage()),
  );
  const pages = [host, stage, ...players];
  for (const page of pages) {
    const pageMessages: unknown[] = [];
    messagesByPage.push(pageMessages);
    captureDmMessages(page, dmMessages, pageMessages);
  }

  const roomCode = await createRoom(host);
  const pairingCode = await createDisplayPairing(host);
  for (const [index, page] of players.entries()) {
    await joinAndReady(page, index + 1, roomCode);
  }
  await confirmAllRoles(playerContexts);
  await pairStage(stage, roomCode, pairingCode);
  await playNight(playerContexts, 2, {
    seerTarget: 1,
    witchAction: options.witchAction ?? "skip",
  });
  await injectSeatDm(options.request, roomCode, 1);
  await expect
    .poll(() => {
      return dmMessages.some(
        (payload) =>
          (
            payload as {
              message: { channel: string };
            }
          ).message.channel === "seat",
      );
    })
    .toBe(true);

  return {
    roomCode,
    host,
    stage,
    players,
    playerContexts,
    dmMessages,
    messagesByPage,
    pages,
    cleanup: registerCleanup(async () => {
      await Promise.all(contexts.map((context) => context.close()));
    }),
  };
}

async function resetDeterministicE2EState(
  request: APIRequestContext,
): Promise<void> {
  const response = await request.post(
    "http://127.0.0.1:8000/__test__/reset",
    { headers: controlHeaders },
  );
  expect(response.ok()).toBe(true);
}

async function injectSeatDm(
  request: APIRequestContext,
  roomCode: string,
  seatId: number,
): Promise<void> {
  const response = await request.post(
    `http://127.0.0.1:8000/__test__/rooms/${roomCode}/seat-dm`,
    {
      headers: controlHeaders,
      data: { seat_id: seatId },
    },
  );
  if (!response.ok()) {
    throw new Error(
      `injectSeatDm failed: ${response.status()} ${await response.text()}`,
    );
  }
}

async function runDeterministicTemplateFlow({
  browser,
  request,
}: {
  browser: Browser;
  request: APIRequestContext;
}): Promise<DeterministicRun> {
  await resetDeterministicE2EState(request);
  const flow = await completeTemplateOnlyFlow({
    browser,
    request,
    deterministicCommandIds: true,
  });
  const uniqueById = new Map<string, unknown>();
  for (const payload of flow.dmMessages) {
    const messageId = (
      payload as {
        message: { message_id: string };
      }
    ).message.message_id;
    uniqueById.set(messageId, payload);
  }
  const dmMessages = [...uniqueById.values()].sort((left, right) => {
    const leftSeq = (left as { outbox_seq: number }).outbox_seq;
    const rightSeq = (right as { outbox_seq: number }).outbox_seq;
    if (leftSeq !== rightSeq) return leftSeq - rightSeq;
    return String(left).localeCompare(String(right));
  });
  const playerPublicMessages = flow.messagesByPage.slice(2).map((messages) =>
    messages.filter(
      (payload) =>
        (
          payload as {
            message?: { channel?: unknown };
          }
        ).message?.channel === "public",
    ),
  );
  return {
    roomCode: flow.roomCode,
    dmMessages,
    playerPublicMessages,
    cleanup: flow.cleanup,
  };
}

async function runRecoveryOrderingFlow({
  browser,
  request,
}: {
  browser: Browser;
  request: APIRequestContext;
}): Promise<RecoveryRun> {
  await resetDeterministicE2EState(request);
  const flow = await completeTemplateOnlyFlow({
    browser,
    request,
    deterministicCommandIds: true,
    witchAction: "antidote",
  });

  const stageBoundary = (): number =>
    flow.messagesByPage[1].reduce<number>(
      (maximum, message) =>
        Math.max(maximum, (message as { outbox_seq: number }).outbox_seq),
      0,
    );
  await flow.host.getByRole("button", { name: "暂停游戏" }).click();
  await expect(flow.stage.getByTestId("stage-pause-banner")).toBeVisible();
  const pauseBoundary = stageBoundary();
  const paused = await flow.stage
    .getByTestId("stage-pause-banner")
    .isVisible();
  await flow.host.getByRole("button", { name: "恢复游戏" }).click();
  await flow.players[1].reload();
  await expect(
    flow.players[1].getByRole("heading", { name: "玩家席" }),
  ).toBeVisible();
  await expect(flow.players[1].getByText("已连接")).toBeVisible();
  const reconnected = await flow.players[1]
    .getByText("已连接")
    .isVisible();
  const reconnectBoundary = stageBoundary();

  await advanceRoom(request, flow.roomCode, 45);
  await passDiscussion(flow.playerContexts);
  await submitNormalVotes(
    flow.playerContexts,
    { 1: 4, 2: 4, 3: 4, 4: 1, 5: 1, 6: 1 },
    "h2:text-is('PK 讨论')",
  );
  await passDiscussion(flow.playerContexts, "PK 讨论");
  await submitPkVotes(
    flow.playerContexts,
    { 2: 1, 3: 1, 5: 4, 6: 4 },
    "[data-action='WOLF_NOMINATE_KILL']:visible",
  );
  await waitForNight(flow.playerContexts);
  await playNight(flow.playerContexts, 2, {
    seerTarget: 1,
    witchAction: "skip",
  });
  await advanceDiscussionByTimeout(
    flow.playerContexts,
    request,
    flow.roomCode,
  );
  await passDiscussion(flow.playerContexts);
  await submitNormalVotes(
    flow.playerContexts,
    { 1: 4, 3: 4, 4: 1, 5: 4, 6: 4 },
    "h2:text-is('终局结果')",
  );
  await waitForGameEnd(flow.playerContexts);

  return {
    dmMessagesByPage: flow.messagesByPage.map((messages) => [...messages]),
    pauseBoundary,
    reconnectBoundary,
    paused,
    reconnected,
    cleanup: flow.cleanup,
  };
}

test("strict dm.message parser accepts only authorized public and seat frames", () => {
  expect(parseServerMessage(publicDm)).toEqual({
    kind: "message",
    message: publicDm,
  });
  expect(parseServerMessage(validSeatDm)).toEqual({
    kind: "message",
    message: validSeatDm,
  });
});

test("strict dm.message parser ignores malformed frames without throwing", () => {
  const missingEnvelope = Object.fromEntries(
    Object.entries(publicDm).filter(([key]) => key !== "outbox_seq"),
  );
  const missingAudienceMessage = Object.fromEntries(
    Object.entries(publicDm.message).filter(
      ([key]) => key !== "audience_bindings",
    ),
  );
  const malformed = [
    missingEnvelope,
    { ...publicDm, message: missingAudienceMessage },
    { ...publicDm, type: "unknown.message" },
    { ...publicDm, unexpected: true },
    { ...publicDm, outbox_seq: 7.5 },
    { ...publicDm, message: { ...publicDm.message, revision: "6" } },
    { ...publicDm, message: { ...publicDm.message, text: "" } },
    { ...publicDm, message: { ...publicDm.message, source: "provider" } },
    { ...publicDm, message: { ...publicDm.message, extra: true } },
    { ...publicDm, message: { ...publicDm.message, channel: "seat" } },
    {
      ...publicDm,
      message: {
        ...publicDm.message,
        audience_bindings: [{ seat_id: 1, session_id: "session-1" }],
      },
    },
    {
      ...publicDm,
      message: {
        ...publicDm.message,
        channel: "seat",
        audience_bindings: [{ seat_id: 7, session_id: "session" }],
      },
    },
    {
      ...publicDm,
      message: {
        ...publicDm.message,
        channel: "seat",
        audience_bindings: [
          { seat_id: 1, session_id: "session-1" },
          { seat_id: 2, session_id: "session-2" },
        ],
      },
    },
    {
      ...publicDm,
      message: {
        ...publicDm.message,
        channel: "seat",
        audience_bindings: [{ seat_id: 1, session_id: 7 }],
      },
    },
  ];

  for (const payload of malformed) {
    expect(() => parseServerMessage(payload)).not.toThrow();
    expect(parseServerMessage(payload)).toEqual({ kind: "ignored" });
  }
  expect(parseServerMessage({ type: "future.message" })).toEqual({
    kind: "ignored",
  });
});

test("public dm.message renders only on the public stage", async ({
  browser,
  request,
}) => {
  const flow = await completeTemplateOnlyFlow({ browser, request });
  const publicMessage = flow.stage
    .getByTestId("dm-message")
    .filter({ hasText: "昨夜,2 号玩家出局。" });
  await expect(publicMessage).toHaveAttribute("data-source", "template");
  for (const player of flow.players) {
    await expect(
      player.locator('[data-testid="dm-message"][data-channel="public"]'),
    ).toHaveCount(0);
  }
  await flow.cleanup();
});

test("seat dm.message renders only on the matching seat", async ({
  browser,
  request,
}) => {
  const flow = await completeTemplateOnlyFlow({ browser, request });
  const targetText = "请 1 号玩家在 女巫行动 行动。";
  for (const [index, player] of flow.players.entries()) {
    const matches = player
      .getByTestId("dm-message")
      .filter({ hasText: targetText });
    await expect(matches).toHaveCount(index === 0 ? 1 : 0);
  }
  await expect(
    flow.stage.getByTestId("dm-message").filter({ hasText: targetText }),
  ).toHaveCount(0);
  await flow.cleanup();
});

test("dm DOM excludes trace provider token session and private metadata", async ({
  browser,
  request,
}) => {
  const capture = await completeTemplateOnlyFlow({ browser, request });
  await expect
    .poll(() =>
      capture.dmMessages.some(
        (payload) =>
          (
            payload as {
              message?: { channel?: unknown };
            }
          ).message?.channel === "seat",
      ),
    )
    .toBe(true);
  const seatSessionId = (
    capture.dmMessages.find(
      (payload) =>
        (
          payload as {
            message?: { channel?: unknown };
          }
        ).message?.channel === "seat",
    ) as {
      message: { audience_bindings: Array<{ session_id: string }> };
    }
  ).message.audience_bindings[0].session_id;
  const forbidden = [
    "dm_trace",
    "dm_transport_trace",
    "provider",
    "prompt",
    "raw_output",
    "private_facts",
    seatSessionId,
  ];
  for (const page of capture.pages) {
    const html = await page.locator("body").innerHTML();
    for (const value of forbidden) {
      expect(html).not.toContain(value);
    }
    expect(html).not.toMatch(/token-\d+/);
    expect(html).not.toContain("audience_bindings");
  }
  await capture.cleanup();
});

test("provider hosts receive zero requests", async ({
  browser,
  request,
}) => {
  const providerRequests: string[] = [];
  const unexpectedExternalRequests: string[] = [];
  const webSocketHosts: string[] = [];
  const blockedWebSocketUrls: string[] = [];
  const blockedHosts = [
    "openai.com",
    "deepseek.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
  ];
  const flow = await completeTemplateOnlyFlow({
    browser,
    request,
    onContext: async (context) => {
      await context.routeWebSocket("**/*", (socket) => {
        const host = new URL(socket.url()).hostname;
        webSocketHosts.push(host);
        if (
          blockedHosts.some(
            (blocked) => host === blocked || host.endsWith(`.${blocked}`),
          ) ||
          (host !== "127.0.0.1" && host !== "localhost")
        ) {
          blockedWebSocketUrls.push(socket.url());
          socket.close();
          return;
        }
        socket.connectToServer();
      });
      await context.route("**/*", async (route) => {
        const host = new URL(route.request().url()).hostname;
        if (
          blockedHosts.some(
            (blocked) => host === blocked || host.endsWith(`.${blocked}`),
          )
        ) {
          providerRequests.push(route.request().url());
          await route.abort();
          return;
        }
        if (host !== "127.0.0.1" && host !== "localhost") {
          unexpectedExternalRequests.push(route.request().url());
          await route.abort();
          return;
        }
        await route.continue();
      });
    },
  });
  expect(providerRequests).toEqual([]);
  expect(unexpectedExternalRequests).toEqual([]);
  expect(blockedWebSocketUrls).toEqual([]);
  expect(
    webSocketHosts.every(
      (host) => host === "127.0.0.1" || host === "localhost",
    ),
  ).toBe(true);
  await flow.cleanup();
});

test("fixed room seed session and catalog produce deep-equal dm messages", async ({
  browser,
  request,
}) => {
  const first = await runDeterministicTemplateFlow({ browser, request });
  await first.cleanup();
  const second = await runDeterministicTemplateFlow({ browser, request });
  expect(first.dmMessages.length).toBeGreaterThan(0);
  expect(JSON.stringify(second.dmMessages)).toBe(
    JSON.stringify(first.dmMessages),
  );
  await second.cleanup();
});

test("six players observe the same public template text at one revision", async ({
  browser,
  request,
}) => {
  const run = await runDeterministicTemplateFlow({ browser, request });
  expect(run.playerPublicMessages).toHaveLength(6);
  for (const messages of run.playerPublicMessages) {
    expect(messages.length).toBeGreaterThan(0);
  }
  const firstMessage = run.playerPublicMessages[0][0] as {
    message: { revision: number };
  };
  for (const messages of run.playerPublicMessages) {
    expect((messages[0] as { message: { revision: number } }).message.revision)
      .toBe(firstMessage.message.revision);
    expect(messages[0]).toEqual(run.playerPublicMessages[0][0]);
  }
  await run.cleanup();
});

test("pause reconnect and game end preserve dm ordering", async ({
  browser,
  request,
}) => {
  const run = await runRecoveryOrderingFlow({ browser, request });
  const playerMessages = run.dmMessagesByPage.slice(2);
  for (const messages of playerMessages) {
    expect(messages.length).toBeGreaterThan(0);
    const sequences = messages.map(
      (message) => (message as { outbox_seq: number }).outbox_seq,
    );
    for (let index = 1; index < sequences.length; index += 1) {
      expect(sequences[index]).toBeGreaterThan(sequences[index - 1]);
    }
    expect(new Set(sequences).size).toBe(sequences.length);
  }
  expect(run.paused).toBe(true);
  expect(run.reconnected).toBe(true);
  const stageMessages = run.dmMessagesByPage[1];
  await expect
    .poll(() =>
      stageMessages.some((message) =>
        (
          message as {
            message: { text: string };
          }
        ).message.text.includes("游戏结束"),
      ),
    )
    .toBe(true);
  const terminalIndex = stageMessages.findIndex((message) =>
    (
      message as {
        message: { text: string };
      }
    ).message.text.includes("游戏结束"),
  );
  expect(terminalIndex).toBeGreaterThanOrEqual(0);
  expect(run.pauseBoundary).toBeGreaterThanOrEqual(0);
  expect(
    (
      stageMessages[terminalIndex] as {
        outbox_seq: number;
      }
    ).outbox_seq,
  ).toBeGreaterThan(run.pauseBoundary);
  expect(
    (
      stageMessages[terminalIndex] as {
        outbox_seq: number;
      }
    ).outbox_seq,
  ).toBeGreaterThanOrEqual(run.reconnectBoundary);
  await run.cleanup();
});
