import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import { toAppError } from "../../protocol/errors";
import type {
  CommandPayload,
  LegalAction,
  SeatView,
} from "../../protocol/models";
import type {
  RoomSocket,
  RoomSocketEvent,
  RoomSocketState,
} from "../../realtime/RoomSocket";
import { useRoomSocket } from "../../realtime/useRoomSocket";
import {
  readSeatSession,
  type StoredSeatSession,
} from "../../session/storage";

import {
  dayPayload,
  isDayActionName,
} from "./DayDiscussionScreen";
import { JoinRoomScreen } from "./JoinRoomScreen";
import { nightPayload } from "./NightActionScreen";
import { PlayerLobbyScreen } from "./PlayerLobbyScreen";
import { SeatShell } from "./SeatShell";

const SEAT_SESSION_KEY_PREFIX = "werewolf:v1:room";
const SEAT_DISPLAY_NAME_SUFFIX = "seat-name";

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

function clearStoredSeatSession(roomCode: string): void {
  try {
    globalThis.localStorage.removeItem(
      `${SEAT_SESSION_KEY_PREFIX}:${roomCode}:seat`,
    );
    globalThis.localStorage.removeItem(
      `${SEAT_SESSION_KEY_PREFIX}:${roomCode}:${SEAT_DISPLAY_NAME_SUFFIX}`,
    );
  } catch {
    // Storage can be unavailable or read-only; the UI still exits the session.
  }
}

function readStoredDisplayName(roomCode: string): string {
  if (roomCode.length === 0) return "";
  try {
    return (
      globalThis.localStorage.getItem(
        `${SEAT_SESSION_KEY_PREFIX}:${roomCode}:${SEAT_DISPLAY_NAME_SUFFIX}`,
      ) ?? ""
    );
  } catch {
    return "";
  }
}

function writeStoredDisplayName(roomCode: string, displayName: string): void {
  try {
    globalThis.localStorage.setItem(
      `${SEAT_SESSION_KEY_PREFIX}:${roomCode}:${SEAT_DISPLAY_NAME_SUFFIX}`,
      displayName,
    );
  } catch {
    // Storage can be unavailable or read-only; the live session still works.
  }
}

export function PlayerRoute() {
  const { roomCode: routeCode } = useParams();
  const location = useLocation();
  const roomCode = normalizeRoomCode(routeCode);
  const playerRoute = location.pathname.startsWith("/play/");
  const [session, setSession] = useState<StoredSeatSession | null>(() =>
    roomCode.length > 0 ? readSeatSession(roomCode) : null,
  );
  const [connectionState, setConnectionState] =
    useState<RoomSocketState>("idle");
  const [seatView, setSeatView] = useState<SeatView | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [joinPending, setJoinPending] = useState(false);
  const [ready, setReady] = useState(false);
  const [confirmPending, setConfirmPending] = useState(false);
  const [roleConfirmed, setRoleConfirmed] = useState(false);
  const [nightPending, setNightPending] = useState(false);
  const [dayPending, setDayPending] = useState(false);
  const [displayName, setDisplayName] = useState(() =>
    readStoredDisplayName(roomCode),
  );
  const socketRef = useRef<RoomSocket | null>(null);
  const seatViewRef = useRef<SeatView | null>(null);
  const displayNameRef = useRef(displayName);
  const joinIntentRef = useRef<{
    session: StoredSeatSession;
    displayName: string;
  } | null>(null);
  const joinCommandIdRef = useRef<string | null>(null);
  const readyCommandRef = useRef<{
    commandId: string;
    previousReady: boolean;
    nextReady: boolean;
  } | null>(null);
  const confirmCommandRef = useRef<string | null>(null);
  const nightIntentRef = useRef<{
    action: LegalAction;
    targetSeatId: number | null;
  } | null>(null);
  const nightCommandRef = useRef<string | null>(null);
  const dayIntentRef = useRef<{
    action: LegalAction;
    targetSeatId: number | null;
    text: string | null;
  } | null>(null);
  const dayCommandRef = useRef<string | null>(null);

  const sendCommand = useCallback(
    (
      payload: CommandPayload,
      expectedRevision: number,
      commandSession: StoredSeatSession,
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
      return sent ? id : null;
    },
    [],
  );

  const sendNightAction = useCallback(
    (
      action: LegalAction,
      targetSeatId: number | null,
      expectedRevision: number,
      commandSession: StoredSeatSession,
    ): string | null => {
      let payload;
      try {
        payload = nightPayload(action, targetSeatId);
      } catch {
        setErrorMessage("行动目标无效，请重新选择");
        return null;
      }

      const id = sendCommand(payload, expectedRevision, commandSession);
      if (id === null) {
        setErrorMessage("连接尚未就绪，请重试");
        return null;
      }
      nightIntentRef.current = { action, targetSeatId };
      nightCommandRef.current = id;
      setNightPending(true);
      setErrorMessage(null);
      return id;
    },
    [sendCommand],
  );

  const sendDayAction = useCallback(
    (
      action: LegalAction,
      targetSeatId: number | null,
      text: string | null,
      expectedRevision: number,
      commandSession: StoredSeatSession,
    ): string | null => {
      let payload;
      try {
        payload = dayPayload(action, targetSeatId, text);
      } catch {
        setErrorMessage("行动内容无效，请重新输入");
        return null;
      }

      const id = sendCommand(payload, expectedRevision, commandSession);
      if (id === null) {
        setErrorMessage("连接尚未就绪，请重试");
        return null;
      }
      dayIntentRef.current = { action, targetSeatId, text };
      dayCommandRef.current = id;
      setDayPending(true);
      setErrorMessage(null);
      return id;
    },
    [sendCommand],
  );

  const expireSession = useCallback((expiredSession: StoredSeatSession | null) => {
    if (expiredSession !== null) {
      clearStoredSeatSession(expiredSession.roomCode);
    }
    joinIntentRef.current = null;
    joinCommandIdRef.current = null;
    readyCommandRef.current = null;
    confirmCommandRef.current = null;
    nightIntentRef.current = null;
    nightCommandRef.current = null;
    dayIntentRef.current = null;
    dayCommandRef.current = null;
    setJoinPending(false);
    setReady(false);
    setConfirmPending(false);
    setRoleConfirmed(false);
    setNightPending(false);
    setDayPending(false);
    setSeatView(null);
    seatViewRef.current = null;
    setErrorMessage("会话已过期，请重新加入");
    setSession(null);
  }, []);

  const handleSocketEvent = useCallback(
    (event: RoomSocketEvent) => {
      if (event.kind === "state") {
        setConnectionState(event.state);
        if (event.closeCode === 4001) {
          expireSession(session);
        } else if (event.closeCode !== null) {
          joinCommandIdRef.current = null;
          confirmCommandRef.current = null;
          nightIntentRef.current = null;
          nightCommandRef.current = null;
          dayIntentRef.current = null;
          dayCommandRef.current = null;
          setConfirmPending(false);
          setNightPending(false);
          setDayPending(false);
        }
        return;
      }

      const message = event.message;
      if (message.type === "session.ready") {
        setConnectionState("ready");
        setSeatView(message.snapshot.seat_view);
        seatViewRef.current = message.snapshot.seat_view;

        const joinIntent = joinIntentRef.current;
        const seatViewHasJoin =
          message.snapshot.seat_view?.legal_actions.some(
            (action) => action.action === "JOIN_ROOM",
          ) === true;
        const resumedJoinIntent =
          joinIntent ??
          (session !== null && seatViewHasJoin && displayNameRef.current.length > 0
            ? {
                session,
                displayName: displayNameRef.current,
              }
            : null);
        if (resumedJoinIntent !== null) {
          joinIntentRef.current = resumedJoinIntent;
          setJoinPending(true);
          const id = sendCommand(
            {
              command_type: "JOIN_ROOM",
              seat_id: resumedJoinIntent.session.seatId,
              display_name: resumedJoinIntent.displayName,
            },
            message.snapshot.revision,
            resumedJoinIntent.session,
          );
          joinCommandIdRef.current = id;
          if (id === null) {
            setErrorMessage("连接尚未就绪，请重试");
          }
        }
        return;
      }

      if (message.type === "seat.view.updated") {
        if (
          session !== null &&
          message.seat_id === session.seatId &&
          message.seat_view.room_id === session.roomId
        ) {
          setSeatView(message.seat_view);
          seatViewRef.current = message.seat_view;
          setErrorMessage(null);
          if (
            joinIntentRef.current !== null &&
            joinCommandIdRef.current === null
          ) {
            const joinIntent = joinIntentRef.current;
            const id = sendCommand(
              {
                command_type: "JOIN_ROOM",
                seat_id: joinIntent.session.seatId,
                display_name: joinIntent.displayName,
              },
              message.seat_view.revision,
              joinIntent.session,
            );
            joinCommandIdRef.current = id;
          }
        }
        return;
      }

      if (message.type === "command.ack") {
        if (message.accepted) {
          if (message.command_id === joinCommandIdRef.current) {
            joinIntentRef.current = null;
            joinCommandIdRef.current = null;
            setJoinPending(false);
            setErrorMessage(null);
          }
          const readyCommand = readyCommandRef.current;
          if (
            readyCommand !== null &&
            message.command_id === readyCommand.commandId
          ) {
            setReady(readyCommand.nextReady);
            readyCommandRef.current = null;
            setErrorMessage(null);
          }
          if (message.command_id === confirmCommandRef.current) {
            confirmCommandRef.current = null;
            setConfirmPending(false);
            setRoleConfirmed(true);
            setErrorMessage(null);
          }
          if (message.command_id === nightCommandRef.current) {
            nightCommandRef.current = null;
            nightIntentRef.current = null;
            setNightPending(false);
            setErrorMessage(null);
          }
          if (message.command_id === dayCommandRef.current) {
            dayCommandRef.current = null;
            dayIntentRef.current = null;
            setDayPending(false);
            setErrorMessage(null);
          }
          return;
        }

        if (message.command_id === joinCommandIdRef.current) {
          joinCommandIdRef.current = null;
          setErrorMessage(
            toAppError({ code: message.error_code }, 0).message,
          );
          if (
            message.error_code === "REVISION_CONFLICT" &&
            joinIntentRef.current !== null &&
            seatViewRef.current !== null
          ) {
            const joinIntent = joinIntentRef.current;
            const id = sendCommand(
              {
                command_type: "JOIN_ROOM",
                seat_id: joinIntent.session.seatId,
                display_name: joinIntent.displayName,
              },
              seatViewRef.current.revision,
              joinIntent.session,
            );
            joinCommandIdRef.current = id;
          } else if (message.error_code !== "REVISION_CONFLICT") {
            joinIntentRef.current = null;
            setJoinPending(false);
          }
          return;
        }

        const readyCommand = readyCommandRef.current;
        if (
          readyCommand !== null &&
          message.command_id === readyCommand.commandId
        ) {
          setReady(readyCommand.previousReady);
          readyCommandRef.current = null;
        }
        if (message.command_id === confirmCommandRef.current) {
          confirmCommandRef.current = null;
          setConfirmPending(false);
        }
        if (message.command_id === nightCommandRef.current) {
          const nightIntent = nightIntentRef.current;
          const latestView = seatViewRef.current;
          const latestAction =
            nightIntent === null || latestView === null
              ? null
              : latestView.legal_actions.find(
                  (action) =>
                    action.action === nightIntent.action.action,
                ) ?? null;
          nightCommandRef.current = null;
          nightIntentRef.current = null;
          setNightPending(false);
          if (
            message.error_code === "REVISION_CONFLICT" &&
            nightIntent !== null &&
            latestView !== null &&
            session !== null
          ) {
            if (latestAction === null) {
              setErrorMessage("行动已失效，请重新选择");
              return;
            }
            try {
              nightPayload(latestAction, nightIntent.targetSeatId);
            } catch {
              setErrorMessage("行动已失效，请重新选择");
              return;
            }
            sendNightAction(
              latestAction,
              nightIntent.targetSeatId,
              latestView.revision,
              session,
            );
            return;
          }
        }
        if (message.command_id === dayCommandRef.current) {
          const dayIntent = dayIntentRef.current;
          const latestView = seatViewRef.current;
          const latestAction =
            dayIntent === null || latestView === null
              ? null
              : latestView.legal_actions.find(
                  (action) => action.action === dayIntent.action.action,
                ) ?? null;
          dayCommandRef.current = null;
          dayIntentRef.current = null;
          setDayPending(false);
          if (
            message.error_code === "REVISION_CONFLICT" &&
            dayIntent !== null &&
            latestView !== null &&
            session !== null
          ) {
            if (latestAction === null) {
              setErrorMessage("行动已失效，请重新选择");
              return;
            }
            try {
              dayPayload(
                latestAction,
                dayIntent.targetSeatId,
                dayIntent.text,
              );
            } catch {
              setErrorMessage("行动已失效，请重新选择");
              return;
            }
            sendDayAction(
              latestAction,
              dayIntent.targetSeatId,
              dayIntent.text,
              latestView.revision,
              session,
            );
            return;
          }
        }
        setErrorMessage(toAppError({ code: message.error_code }, 0).message);
        return;
      }

      if (message.type === "error") {
        if (message.code === "TOKEN_INVALID" || message.code === "TOKEN_EXPIRED") {
          expireSession(session);
          return;
        }
        setErrorMessage(toAppError({ code: message.code }, 0).message);
      }
    },
    [
      expireSession,
      sendCommand,
      sendDayAction,
      sendNightAction,
      session,
    ],
  );

  const socketConfig =
    session === null
      ? null
      : {
          url: websocketUrl(),
          roomCode: session.roomCode,
          role: "seat" as const,
          token: session.token,
        };
  const roomSocket = useRoomSocket(socketConfig, handleSocketEvent);

  useEffect(() => {
    socketRef.current = roomSocket;
  }, [roomSocket]);

  useEffect(() => {
    return () => {
      joinIntentRef.current = null;
      joinCommandIdRef.current = null;
      readyCommandRef.current = null;
      confirmCommandRef.current = null;
      nightIntentRef.current = null;
      nightCommandRef.current = null;
      dayIntentRef.current = null;
      dayCommandRef.current = null;
    };
  }, []);

  function handleJoined(
    joinedSession: StoredSeatSession,
    displayName: string,
  ) {
    joinIntentRef.current = {
      session: joinedSession,
      displayName,
    };
    displayNameRef.current = displayName;
    setDisplayName(displayName);
    writeStoredDisplayName(joinedSession.roomCode, displayName);
    setSession(joinedSession);
    setJoinPending(true);
    setSeatView(null);
    seatViewRef.current = null;
    setReady(false);
    setConfirmPending(false);
    setRoleConfirmed(false);
    setNightPending(false);
    setDayPending(false);
    setErrorMessage(null);
  }

  function handleToggleReady() {
    if (session === null || seatView === null) return;
    const nextReady = !ready;
    const id = sendCommand(
      { command_type: "SET_READY", ready: nextReady },
      seatView.revision,
      session,
    );
    if (id === null) {
      setErrorMessage("连接尚未就绪，请重试");
      return;
    }
    readyCommandRef.current = {
      commandId: id,
      previousReady: ready,
      nextReady,
    };
  }

  function handleSeatAction(
    action: LegalAction,
    targetSeatId: number | null = null,
    text: string | null = null,
  ) {
    if (session === null || seatView === null) {
      return;
    }

    if (isDayActionName(action.action)) {
      if (dayPending || dayCommandRef.current !== null) return;
      sendDayAction(
        action,
        targetSeatId,
        text,
        seatView.revision,
        session,
      );
      return;
    }

    if (action.action !== "CONFIRM_ROLE") {
      if (nightPending || nightCommandRef.current !== null) return;
      sendNightAction(
        action,
        targetSeatId,
        seatView.revision,
        session,
      );
      return;
    }

    if (
      confirmPending ||
      roleConfirmed ||
      confirmCommandRef.current !== null
    ) {
      return;
    }
    const id = sendCommand(
      { command_type: "CONFIRM_ROLE" },
      seatView.revision,
      session,
    );
    if (id === null) {
      setErrorMessage("连接尚未就绪，请重试");
      return;
    }
    confirmCommandRef.current = id;
    setConfirmPending(true);
    setErrorMessage(null);
  }

  if (session === null) {
    return (
      <JoinRoomScreen
        expiredSession={playerRoute}
        initialRoomCode={roomCode}
        onJoined={handleJoined}
      />
    );
  }

  if (seatView !== null && seatView.phase !== "LOBBY") {
    return (
      <SeatShell
        actionConfirmed={roleConfirmed}
        actionPending={confirmPending || nightPending || dayPending}
        connectionState={connectionState}
        errorMessage={errorMessage}
        onAction={handleSeatAction}
        ready={ready}
        roomCode={session.roomCode}
        seatId={session.seatId}
        seatView={seatView}
      />
    );
  }

  return (
    <PlayerLobbyScreen
      connectionState={connectionState}
      errorMessage={errorMessage}
      joinPending={joinPending}
      onToggleReady={handleToggleReady}
      ready={ready}
      roomCode={session.roomCode}
      seatId={session.seatId}
      seatView={seatView}
    />
  );
}
