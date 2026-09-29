import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

import { controlHeaders } from "./support/fixtures";
import {
  applyPotionRecoveryPatch,
  createDisplayPairing,
  createRoom,
  joinAndReady,
  pairStage,
  pauseHostGame,
  resetE2EState,
} from "./support/flows";

interface RuntimeState {
  database_path: string;
  process_id: number;
}

interface HostAudit {
  revision: number;
  raw_events: Array<{ event_id: string }>;
  recovery_audit: Array<{ record_id: string; status: string }>;
  snapshots: Array<{ snapshot_id: string; reason: string }>;
}

function observeServerFrames(page: Page): string[] {
  const frames: string[] = [];
  page.on("websocket", (socket) => {
    socket.on("framereceived", (event) => {
      frames.push(event.payload.toString());
    });
  });
  return frames;
}

function revisionsFor(
  frames: string[],
  messageType: "host.control.updated" | "public.view.updated" | "seat.view.updated",
): number[] {
  return frames.flatMap((frame) => {
    try {
      const message = JSON.parse(frame) as {
        type?: string;
        host_control?: { revision?: number };
        public_view?: { revision?: number };
        seat_view?: { revision?: number };
      };
      if (message.type !== messageType) return [];
      if (messageType === "host.control.updated") {
        return message.host_control?.revision === undefined
          ? []
          : [message.host_control.revision];
      }
      if (messageType === "seat.view.updated") {
        return message.seat_view?.revision === undefined
          ? []
          : [message.seat_view.revision];
      }
      return message.public_view?.revision === undefined
        ? []
        : [message.public_view.revision];
    } catch {
      return [];
    }
  });
}

function readyRevisionsFor(
  frames: string[],
  view: "seat" | "public",
): number[] {
  return frames.flatMap((frame) => {
    try {
      const message = JSON.parse(frame) as {
        type?: string;
        snapshot?: {
          public_view?: { revision?: number };
          seat_view?: { revision?: number } | null;
        };
      };
      if (message.type !== "session.ready" || message.snapshot === undefined) {
        return [];
      }
      const revision =
        view === "seat"
          ? message.snapshot.seat_view?.revision
          : message.snapshot.public_view?.revision;
      return revision === undefined ? [] : [revision];
    } catch {
      return [];
    }
  });
}

function hasAcceptedAck(frames: string[], revision: number): boolean {
  return frames.some((frame) => {
    try {
      const message = JSON.parse(frame) as {
        type?: string;
        accepted?: boolean;
        revision?: number;
      };
      return (
        message.type === "command.ack" &&
        message.accepted === true &&
        message.revision === revision
      );
    } catch {
      return false;
    }
  });
}

function assertNoHostOnlyPayload(frames: string[]): void {
  const serialized = frames.join("\n");
  for (const forbidden of [
    "snapshots",
    "snapshot_id",
    "recovery_audit",
    "raw_events",
    "HOST_CORRECTION_APPLIED",
    "HOST_COMPENSATION_APPLIED",
    "token",
  ]) {
    expect(serialized).not.toContain(forbidden);
  }
}

async function runtimeState(
  request: APIRequestContext,
): Promise<RuntimeState> {
  const response = await request.get(
    "http://127.0.0.1:8000/__test__/runtime",
    { headers: controlHeaders },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as RuntimeState;
}

async function restartApp(
  request: APIRequestContext,
  previousProcessId: number,
): Promise<void> {
  const response = await request.post(
    "http://127.0.0.1:8000/__test__/restart",
    { headers: controlHeaders },
  );
  expect(response.ok()).toBe(true);
  await expect
    .poll(async () => {
      try {
        const runtime = await runtimeState(request);
        return (
          runtime.process_id !== previousProcessId &&
          (await request.get("http://127.0.0.1:8000/healthz")).ok()
        );
      } catch {
        return false;
      }
    }, { timeout: 30_000 })
    .toBe(true);
}

async function hostAudit(
  request: APIRequestContext,
  roomCode: string,
  hostToken: string,
): Promise<HostAudit> {
  const response = await request.get(
    `http://127.0.0.1:8000/rooms/${roomCode}/audit?include=recovery_audit,snapshots`,
    { headers: { Authorization: `Bearer ${hostToken}` } },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as HostAudit;
}

async function storedToken(
  page: Page,
  roomCode: string,
  role: "host" | "seat",
): Promise<string> {
  return page.evaluate(
    ({ key }) => {
      const raw = localStorage.getItem(key);
      if (raw === null) throw new Error(`missing ${key}`);
      const session = JSON.parse(raw) as { token?: string };
      if (typeof session.token !== "string") {
        throw new Error(`invalid ${key}`);
      }
      return session.token;
    },
    {
      key: `werewolf:v1:room:${roomCode}:${role}`,
    },
  );
}

function assertSameAudit(before: HostAudit, after: HostAudit): void {
  expect(after.revision).toBe(before.revision);
  expect(after.raw_events.map((event) => event.event_id)).toEqual(
    before.raw_events.map((event) => event.event_id),
  );
  expect(after.recovery_audit.map((record) => record.record_id)).toEqual(
    before.recovery_audit.map((record) => record.record_id),
  );
  const snapshotIds = after.snapshots.map((snapshot) => snapshot.snapshot_id);
  expect(snapshotIds).toEqual(
    before.snapshots.map((snapshot) => snapshot.snapshot_id),
  );
  expect(new Set(snapshotIds).size).toBe(snapshotIds.length);
}

test(
  "E2E-007 REC-006 REC-007 REC-008 REC-009 REC-013 VIS-013 restart recovery preserves views and privacy",
  async ({ browser, request }, testInfo) => {
    await resetE2EState(request);
    const host = await browser.newContext({
      viewport: { width: 390, height: 844 },
    });
    const player = await browser.newContext({
      viewport: { width: 375, height: 667 },
    });
    const display = await browser.newContext({
      viewport: { width: 1280, height: 720 },
    });

    try {
      const hostPage = await host.newPage();
      const playerPage = await player.newPage();
      const displayPage = await display.newPage();
      const hostFrames = observeServerFrames(hostPage);
      const playerFrames = observeServerFrames(playerPage);
      const displayFrames = observeServerFrames(displayPage);

      const roomCode = await createRoom(hostPage);
      const pairingCode = await createDisplayPairing(hostPage);
      await joinAndReady(playerPage, 1, roomCode);
      await pairStage(displayPage, roomCode, pairingCode);

      await pauseHostGame(hostPage);
      const recoveredRevision = await applyPotionRecoveryPatch(hostPage);
      await expect.poll(
        () => revisionsFor(playerFrames, "seat.view.updated").at(-1),
      ).toBe(recoveredRevision);
      await expect.poll(
        () => revisionsFor(displayFrames, "public.view.updated").at(-1),
      ).toBe(recoveredRevision);
      expect(hasAcceptedAck(hostFrames, recoveredRevision)).toBe(true);

      for (const page of [playerPage, displayPage]) {
        for (const forbidden of [
          "snapshots",
          "snapshot_id",
          "recovery_audit",
          "raw_events",
          "HOST_CORRECTION_APPLIED",
          "HOST_COMPENSATION_APPLIED",
          "token",
        ]) {
          await expect(page.locator("body")).not.toContainText(forbidden);
        }
      }
      assertNoHostOnlyPayload(playerFrames);
      assertNoHostOnlyPayload(displayFrames);

      const hostToken = await storedToken(hostPage, roomCode, "host");
      const before = await hostAudit(request, roomCode, hostToken);
      expect(before.snapshots.some(
        (snapshot) => snapshot.reason === "PRE_CORRECTION",
      )).toBe(true);
      expect(before.recovery_audit.some(
        (record) => record.status === "APPLIED",
      )).toBe(true);

      const firstRuntime = await runtimeState(request);
      const playerFrameBoundary = playerFrames.length;
      const displayFrameBoundary = displayFrames.length;
      await restartApp(request, firstRuntime.process_id);
      const secondRuntime = await runtimeState(request);
      expect(secondRuntime.database_path).toBe(firstRuntime.database_path);
      expect(secondRuntime.process_id).not.toBe(firstRuntime.process_id);

      await Promise.all([
        hostPage.reload(),
        playerPage.reload(),
        displayPage.reload(),
      ]);
      await expect(
        hostPage.getByRole("heading", { name: "主持人控制台" }),
      ).toBeVisible();
      await expect(hostPage.getByTestId("host-revision")).toHaveText(
        String(recoveredRevision),
      );
      await expect(
        hostPage.getByRole("button", { name: "恢复游戏" }),
      ).toBeVisible();
      await expect(
        playerPage.getByRole("heading", { name: "玩家大厅" }),
      ).toBeVisible();
      await expect(displayPage.getByTestId("stage-root")).toBeVisible();
      await expect.poll(
        () => readyRevisionsFor(
          playerFrames.slice(playerFrameBoundary),
          "seat",
        ).at(-1),
      ).toBe(recoveredRevision);
      await expect.poll(
        () => readyRevisionsFor(
          displayFrames.slice(displayFrameBoundary),
          "public",
        ).at(-1),
      ).toBe(recoveredRevision);

      const after = await hostAudit(request, roomCode, hostToken);
      assertSameAudit(before, after);

      const replayResponse = await request.get(
        `http://127.0.0.1:8000/rooms/${roomCode}/replay?include=snapshots,recovery_audit`,
        {
          headers: {
            Authorization: `Bearer ${await storedToken(playerPage, roomCode, "seat")}`,
          },
        },
      );
      expect(replayResponse.ok()).toBe(true);
      const replay = (await replayResponse.json()) as Record<string, unknown>;
      for (const forbidden of [
        "snapshot",
        "recovery_audit",
        "raw_events",
        "state",
        "dm_trace",
        "token",
      ]) {
        expect(replay).not.toHaveProperty(forbidden);
      }
      expect(JSON.stringify(replay)).not.toContain("HOST_CORRECTION_APPLIED");
      expect(JSON.stringify(replay)).not.toContain("HOST_COMPENSATION_APPLIED");

      const seatAudit = await request.get(
        `http://127.0.0.1:8000/rooms/${roomCode}/audit?include=recovery_audit,snapshots`,
        {
          headers: {
            Authorization: `Bearer ${await storedToken(playerPage, roomCode, "seat")}`,
          },
        },
      );
      expect(seatAudit.status()).toBe(403);
      expect(await seatAudit.text()).not.toContain("snapshot");
      expect(await seatAudit.text()).not.toContain("recovery_audit");

      assertNoHostOnlyPayload(playerFrames);
      assertNoHostOnlyPayload(displayFrames);
      await hostPage.screenshot({
        path: testInfo.outputPath("host-recovery-restarted.png"),
        fullPage: true,
      });
    } finally {
      await Promise.all([host.close(), player.close(), display.close()]);
    }
  },
);
