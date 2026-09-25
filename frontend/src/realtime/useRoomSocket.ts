import { useEffect, useMemo } from "react";

import {
  RoomSocket,
  type RoomSocketListener,
  type WebSocketCtor,
} from "./RoomSocket";

export type RoomRole = "seat" | "host" | "display";

export interface UseRoomSocketConfig {
  url: string;
  roomCode: string;
  role: RoomRole;
  token: string | null;
  WebSocketCtor?: WebSocketCtor;
  authTimeoutMs?: number;
}

export function useRoomSocket(
  config: UseRoomSocketConfig | null,
  listener: RoomSocketListener,
): RoomSocket | null {
  const url = config?.url;
  const roomCode = config?.roomCode;
  const role = config?.role;
  const token = config?.token ?? null;
  const WebSocketCtor = config?.WebSocketCtor;
  const authTimeoutMs = config?.authTimeoutMs;

  const roomSocket = useMemo(() => {
    if (url === undefined || roomCode === undefined || role === undefined) {
      return null;
    }
    return new RoomSocket({
      url,
      WebSocketCtor,
      authTimeoutMs,
    });
  }, [url, roomCode, role, WebSocketCtor, authTimeoutMs]);

  useEffect(() => {
    if (roomSocket === null) {
      return;
    }
    return roomSocket.subscribe(listener);
  }, [listener, roomSocket]);

  useEffect(() => {
    if (roomSocket === null || token === null) {
      return;
    }
    roomSocket.connect(token);
    return () => {
      roomSocket.disconnect();
    };
  }, [roomSocket, token]);

  return roomSocket;
}
