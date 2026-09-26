import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AppError, toAppError } from "../../protocol/errors";
import type {
  CommandPayload,
  HostControlView,
} from "../../protocol/models";
import type {
  RoomSocket,
  RoomSocketEvent,
  RoomSocketState,
} from "../../realtime/RoomSocket";
import { useRoomSocket } from "../../realtime/useRoomSocket";
import {
  createDisplayPairing,
  getHostAudit,
  revokeDisplay,
} from "../../session/roomSession";
import {
  readHostSession,
  type StoredHostSession,
} from "../../session/storage";

import { HostControlScreen } from "./HostControlScreen";

type HostCommandPayload = Extract<
  CommandPayload,
  { command_type: "HOST_PAUSE" | "HOST_RESUME" }
>;

interface HostCommandIntent {
  commandId: string;
  payload: HostCommandPayload;
}

const HOST_SESSION_KEY_PREFIX = "werewolf:v1:room";
const LAST_HOST_ROOM_KEY = "werewolf:v1:last-host-room";

function normalizeRoomCode(value: string | undefined): string {
  return value?.trim().toUpperCase() ?? "";
}

function websocketUrl(): string {
  const url = new URL("/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function uuidFromBytes(bytes: Uint8Array): string {
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

function commandId(): string {
  const cryptoApi = globalThis.crypto as Crypto | undefined;
  if (typeof cryptoApi?.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  if (typeof cryptoApi?.getRandomValues === "function") {
    return uuidFromBytes(cryptoApi.getRandomValues(new Uint8Array(16)));
  }
  return `command-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2)}`;
}

function clearStoredHostSession(roomCode: string): void {
  try {
    globalThis.localStorage.removeItem(
      `${HOST_SESSION_KEY_PREFIX}:${roomCode}:host`,
    );
    if (globalThis.localStorage.getItem(LAST_HOST_ROOM_KEY) === roomCode) {
      globalThis.localStorage.removeItem(LAST_HOST_ROOM_KEY);
    }
  } catch {
    // Storage can be unavailable or read-only; the UI still exits the session.
  }
}

function errorMessage(error: unknown): string {
  return error instanceof AppError ? error.message : "操作失败，请重试";
}

export function HostRoute() {
  const { roomCode: routeCode } = useParams();
  const roomCode = normalizeRoomCode(routeCode);
  const [session, setSession] = useState<StoredHostSession | null>(() =>
    roomCode.length > 0 ? readHostSession(roomCode) : null,
  );
  const [connectionState, setConnectionState] =
    useState<RoomSocketState>("idle");
  const [hostControl, setHostControl] = useState<HostControlView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pausePending, setPausePending] = useState(false);
  const [resumePending, setResumePending] = useState(false);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingExpiresAt, setPairingExpiresAt] = useState<string | null>(
    null,
  );
  const [pairingExpired, setPairingExpired] = useState(false);
  const [pairingPending, setPairingPending] = useState(false);
  const [revokePending, setRevokePending] = useState(false);
  const [auditPending, setAuditPending] = useState(false);
  const socketRef = useRef<RoomSocket | null>(null);
  const hostControlRef = useRef<HostControlView | null>(null);
  const hostCommandRef = useRef<HostCommandIntent | null>(null);

  const endSession = useCallback(
    (
      endedSession: StoredHostSession | null,
      message: string,
    ) => {
      if (endedSession !== null) {
        clearStoredHostSession(endedSession.roomCode);
      }
      hostCommandRef.current = null;
      setPausePending(false);
      setResumePending(false);
      setPairingCode(null);
      setPairingExpiresAt(null);
      setPairingExpired(false);
      setHostControl(null);
      hostControlRef.current = null;
      setError(message);
      setSession(null);
    },
    [],
  );

  const expireSession = useCallback(
    (expiredSession: StoredHostSession | null) => {
      endSession(expiredSession, "主持人会话已失效，请重新创建房间。");
    },
    [endSession],
  );

  const sendHostCommand = useCallback(
    (
      payload: HostCommandPayload,
      expectedRevision: number,
      commandSession: StoredHostSession,
    ): string | null => {
      const socket = socketRef.current;
      if (socket === null) return null;

      const id = commandId();
      const sent = socket.send({
        type: "command",
        command: {
          schema_version: "command.v1",
          command_id: id,
          room_id: commandSession.roomId,
          expected_revision: expectedRevision,
          issued_at: new Date().toISOString(),
          payload,
        },
      });
      if (!sent) return null;

      hostCommandRef.current = { commandId: id, payload };
      setPausePending(payload.command_type === "HOST_PAUSE");
      setResumePending(payload.command_type === "HOST_RESUME");
      setError(null);
      return id;
    },
    [],
  );

  const handleSocketEvent = useCallback(
    (event: RoomSocketEvent) => {
      if (event.kind === "state") {
        setConnectionState(event.state);
        if (event.closeCode === 4001) {
          expireSession(session);
        } else if (event.closeCode === 4003) {
          endSession(
            session,
            "主持人控制台已在其他设备接管，请继续使用新设备。",
          );
        } else if (event.closeCode !== null) {
          hostCommandRef.current = null;
          setPausePending(false);
          setResumePending(false);
        }
        return;
      }

      const message = event.message;
      if (message.type === "session.ready") {
        if (
          session === null ||
          message.snapshot.room_id !== session.roomId ||
          message.snapshot.host_control === null
        ) {
          return;
        }
        setHostControl(message.snapshot.host_control);
        hostControlRef.current = message.snapshot.host_control;
        setError(null);
        return;
      }

      if (message.type === "host.control.updated") {
        if (
          session !== null &&
          message.host_control.public_view.room_id === session.roomId
        ) {
          setHostControl(message.host_control);
          hostControlRef.current = message.host_control;
          setError(null);
        }
        return;
      }

      if (message.type === "command.ack") {
        const pending = hostCommandRef.current;
        if (pending === null || message.command_id !== pending.commandId) {
          return;
        }

        hostCommandRef.current = null;
        setPausePending(false);
        setResumePending(false);
        if (message.accepted) {
          setError(null);
          return;
        }

        const latestControl = hostControlRef.current;
        if (
          message.error_code === "REVISION_CONFLICT" &&
          latestControl !== null &&
          session !== null
        ) {
          sendHostCommand(
            pending.payload,
            latestControl.revision,
            session,
          );
          return;
        }

        setError(
          message.error_code === null
            ? "主持人操作失败，请重试"
            : toAppError({ code: message.error_code }, 0).message,
        );
        return;
      }

      if (message.type === "error") {
        if (
          message.code === "TOKEN_INVALID" ||
          message.code === "TOKEN_EXPIRED"
        ) {
          expireSession(session);
          return;
        }
        setError(toAppError({ code: message.code }, 0).message);
      }
    },
    [endSession, expireSession, sendHostCommand, session],
  );

  const socketConfig =
    session === null
      ? null
      : {
          url: websocketUrl(),
          roomCode: session.roomCode,
          role: "host" as const,
          token: session.token,
        };
  const roomSocket = useRoomSocket(socketConfig, handleSocketEvent);

  useEffect(() => {
    socketRef.current = roomSocket;
  }, [roomSocket]);

  useEffect(() => {
    return () => {
      hostCommandRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (pairingExpiresAt === null) return;
    const expiresAt = Date.parse(pairingExpiresAt);
    if (!Number.isFinite(expiresAt)) return;

    let timer: ReturnType<typeof globalThis.setTimeout> | null = null;
    const scheduleExpiry = () => {
      const remaining = expiresAt - Date.now();
      timer = globalThis.setTimeout(
        remaining <= 0
          ? () => {
              setPairingCode(null);
              setPairingExpiresAt(null);
              setPairingExpired(true);
            }
          : scheduleExpiry,
        Math.min(Math.max(remaining, 0), 2_147_000_000),
      );
    };
    scheduleExpiry();
    return () => {
      if (timer !== null) {
        globalThis.clearTimeout(timer);
      }
    };
  }, [pairingExpiresAt]);

  function handlePause() {
    if (session === null || hostControl === null || hostCommandRef.current !== null) {
      return;
    }
    const id = sendHostCommand(
      { command_type: "HOST_PAUSE", reason: "主持人暂停" },
      hostControl.revision,
      session,
    );
    if (id === null) {
      setError("连接尚未就绪，请重试");
    }
  }

  function handleResume() {
    if (session === null || hostControl === null || hostCommandRef.current !== null) {
      return;
    }
    const id = sendHostCommand(
      { command_type: "HOST_RESUME" },
      hostControl.revision,
      session,
    );
    if (id === null) {
      setError("连接尚未就绪，请重试");
    }
  }

  async function handleGeneratePairing() {
    if (session === null || pairingPending) return;
    setPairingPending(true);
    setPairingExpired(false);
    setError(null);
    try {
      const pairing = await createDisplayPairing(
        session.roomCode,
        session.token,
      );
      const expiresAt = Date.parse(pairing.expires_at);
      if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
        setPairingCode(null);
        setPairingExpiresAt(null);
        setPairingExpired(true);
        return;
      }
      setPairingCode(pairing.pairing_code);
      setPairingExpiresAt(pairing.expires_at);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPairingPending(false);
    }
  }

  async function handleRevokeDisplay() {
    if (session === null || revokePending) return;
    setRevokePending(true);
    setError(null);
    try {
      await revokeDisplay(session.roomCode, session.token);
      setPairingCode(null);
      setPairingExpiresAt(null);
      setPairingExpired(false);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setRevokePending(false);
    }
  }

  async function handleDownloadAudit() {
    if (session === null || auditPending) return;
    setAuditPending(true);
    setError(null);
    let objectUrl: string | null = null;
    try {
      const audit = await getHostAudit(session.roomCode, session.token);
      const blob = new Blob([JSON.stringify(audit, null, 2)], {
        type: "application/json",
      });
      objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `host-audit-${session.roomCode}.json`;
      anchor.click();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      if (objectUrl !== null) {
        URL.revokeObjectURL(objectUrl);
      }
      setAuditPending(false);
    }
  }

  if (session === null) {
    return (
      <main className="grid min-h-screen place-items-center bg-surface px-6 py-12 text-text">
        <section className="grid max-w-md gap-4 text-center" role="status">
          <h1 className="text-3xl font-semibold">主持人控制台</h1>
          <p className="text-sm text-text-muted">
            {error ?? "未找到主持人会话，请重新创建房间。"}
          </p>
          <Link
            className="inline-flex min-h-11 items-center justify-center rounded-lg bg-action px-4 font-semibold text-surface"
            to="/"
          >
            返回首页
          </Link>
        </section>
      </main>
    );
  }

  if (hostControl === null) {
    return (
      <main className="grid min-h-screen place-items-center bg-surface px-6 py-12 text-text">
        <section className="grid justify-items-center gap-3 text-center" role="status">
          <h1 className="text-3xl font-semibold">正在连接主持人控制台</h1>
          <p className="text-sm text-text-muted">{`房间码 ${session.roomCode}`}</p>
          {connectionState === "retry_wait" || connectionState === "closed" ? (
            <button
              className="min-h-11 rounded-lg bg-action px-4 font-semibold text-surface"
              onClick={() => roomSocket?.connect(session.token)}
              type="button"
            >
              重新连接
            </button>
          ) : null}
        </section>
      </main>
    );
  }

  return (
    <HostControlScreen
      auditPending={auditPending}
      connectionState={connectionState}
      errorMessage={error}
      hostControl={hostControl}
      onDownloadAudit={() => void handleDownloadAudit()}
      onGeneratePairing={() => void handleGeneratePairing()}
      onPause={handlePause}
      onResume={handleResume}
      onRevokeDisplay={() => void handleRevokeDisplay()}
      pairingCode={pairingCode}
      pairingExpired={pairingExpired}
      pairingPending={pairingPending}
      pausePending={pausePending}
      resumePending={resumePending}
      revokePending={revokePending}
      roomCode={session.roomCode}
    />
  );
}
