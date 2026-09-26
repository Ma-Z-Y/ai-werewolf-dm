import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import type { PublicView } from "../../protocol/models";
import type {
  RoomSocket,
  RoomSocketEvent,
  RoomSocketState,
} from "../../realtime/RoomSocket";
import { useRoomSocket } from "../../realtime/useRoomSocket";
import {
  readDisplaySession,
  type StoredDisplaySession,
} from "../../session/storage";

import { StagePairingScreen } from "./StagePairingScreen";
import { StageShell } from "./StageShell";

const DISPLAY_SESSION_KEY_PREFIX = "werewolf:v1:room";

function normalizeRoomCode(value: string | undefined): string {
  return value?.trim().toUpperCase() ?? "";
}

function websocketUrl(): string {
  const url = new URL("/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function clearStoredDisplaySession(
  session: StoredDisplaySession | null,
): void {
  if (session === null) return;
  try {
    globalThis.sessionStorage.removeItem(
      `${DISPLAY_SESSION_KEY_PREFIX}:${session.roomCode}:display`,
    );
  } catch {
    // Storage can be unavailable or read-only; the UI still exits the session.
  }
}

export function StageRoute() {
  const { roomCode: routeCode } = useParams();
  const roomCode = normalizeRoomCode(routeCode);
  const [session, setSession] = useState<StoredDisplaySession | null>(() =>
    roomCode.length > 0 ? readDisplaySession(roomCode) : null,
  );
  const [connectionState, setConnectionState] =
    useState<RoomSocketState>("idle");
  const [publicView, setPublicView] = useState<PublicView | null>(null);
  const [serverTime, setServerTime] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const socketRef = useRef<RoomSocket | null>(null);

  const endSession = useCallback(
    (
      expiredSession: StoredDisplaySession | null,
      message: string,
    ) => {
      clearStoredDisplaySession(expiredSession);
      setSession(null);
      setPublicView(null);
      setServerTime("");
      setErrorMessage(message);
    },
    [],
  );

  const handleSocketEvent = useCallback(
    (event: RoomSocketEvent) => {
      if (event.kind === "state") {
        setConnectionState(event.state);
        if (event.closeCode === 4001) {
          endSession(session, "共享屏配对已失效，请重新配对");
        } else if (event.closeCode === 4003) {
          endSession(session, "共享屏已在其他设备接管，请重新配对");
        }
        return;
      }

      const message = event.message;
      if (message.type === "session.ready") {
        if (
          session === null ||
          message.snapshot.public_view.room_id !== session.roomId
        ) {
          return;
        }
        setPublicView(message.snapshot.public_view);
        setServerTime(message.server_time);
        setErrorMessage(null);
        socketRef.current?.send({
          type: "subscribe",
          channel: "public",
        });
        return;
      }

      if (message.type === "public.view.updated") {
        if (
          session !== null &&
          message.public_view.room_id === session.roomId
        ) {
          setPublicView(message.public_view);
          setServerTime(message.server_time);
          setErrorMessage(null);
        }
        return;
      }

      if (
        message.type === "error" &&
        (message.code === "TOKEN_INVALID" || message.code === "TOKEN_EXPIRED")
      ) {
        endSession(session, "共享屏配对已失效，请重新配对");
      }
    },
    [endSession, session],
  );

  const socketConfig =
    session === null
      ? null
      : {
          url: websocketUrl(),
          roomCode: session.roomCode,
          role: "display" as const,
          token: session.token,
        };
  const roomSocket = useRoomSocket(socketConfig, handleSocketEvent);

  useEffect(() => {
    socketRef.current = roomSocket;
  }, [roomSocket]);

  if (session === null) {
    return (
      <StagePairingScreen
        onPaired={(pairedSession) => {
          setErrorMessage(null);
          setSession(pairedSession);
        }}
        roomCode={roomCode}
        sessionError={errorMessage}
      />
    );
  }

  if (publicView === null) {
    return (
      <main className="grid min-h-screen place-items-center bg-phase-night px-6 text-text">
        <section className="grid justify-items-center gap-3 text-center" role="status">
          <h1 className="text-3xl font-semibold">正在连接共享屏</h1>
          <p className="text-sm text-text-muted">{`房间 ${session.roomCode}`}</p>
          {connectionState === "retry_wait" || connectionState === "closed" ? (
            <button
              className="min-h-11 rounded-lg bg-action px-4 font-semibold text-surface"
              onClick={() => roomSocket?.connect(session.token)}
              type="button"
            >
              重新连接共享屏
            </button>
          ) : null}
        </section>
      </main>
    );
  }

  return (
    <StageShell
      connectionState={connectionState}
      errorMessage={errorMessage}
      onRetryConnection={() => roomSocket?.connect(session.token)}
      publicView={publicView}
      roomCode={session.roomCode}
      serverTime={serverTime}
    />
  );
}
