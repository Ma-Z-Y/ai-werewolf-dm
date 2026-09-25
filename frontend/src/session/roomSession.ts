import { AppError, toAppError } from "../protocol/errors";
import type {
  CreateRoomResponse,
  DisplayPairingResponse,
  DisplaySessionResponse,
  HostAuditExport,
  JoinRoomResponse,
  PlayerReplay,
} from "../protocol/models";

function roomPath(roomCode: string): string {
  return `/rooms/${encodeURIComponent(roomCode.trim().toUpperCase())}`;
}

async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  try {
    const response = await fetch(path, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(init.headers as Record<string, string> | undefined),
      },
    });

    if (response.status === 204) {
      return undefined as T;
    }

    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw toAppError(payload, response.status);
    }
    if (payload === null) {
      throw new AppError("UNKNOWN_ERROR", response.status);
    }
    return payload as T;
  } catch (error) {
    if (error instanceof AppError) throw error;
    throw new AppError("NETWORK_ERROR", 0);
  }
}

export function createRoom(displayName: string): Promise<CreateRoomResponse> {
  return apiFetch("/rooms", {
    method: "POST",
    body: JSON.stringify({ display_name: displayName.trim() }),
  });
}

export function joinRoom(
  roomCode: string,
  displayName: string,
): Promise<JoinRoomResponse> {
  return apiFetch(`${roomPath(roomCode)}/join`, {
    method: "POST",
    body: JSON.stringify({ display_name: displayName.trim() }),
  });
}

export function createDisplayPairing(
  roomCode: string,
  hostToken: string,
): Promise<DisplayPairingResponse> {
  return apiFetch(`${roomPath(roomCode)}/display-pairings`, {
    method: "POST",
    headers: { Authorization: `Bearer ${hostToken}` },
  });
}

export function exchangeDisplayPairing(
  roomCode: string,
  pairingCode: string,
): Promise<DisplaySessionResponse> {
  return apiFetch(`${roomPath(roomCode)}/display-sessions`, {
    method: "POST",
    body: JSON.stringify({ pairing_code: pairingCode }),
  });
}

export function revokeDisplay(
  roomCode: string,
  hostToken: string,
): Promise<void> {
  return apiFetch(`${roomPath(roomCode)}/display-sessions/current`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${hostToken}` },
  });
}

export function getPlayerReplay(
  roomCode: string,
  seatToken: string,
): Promise<PlayerReplay> {
  return apiFetch(`${roomPath(roomCode)}/replay`, {
    headers: { Authorization: `Bearer ${seatToken}` },
  });
}

export function getHostAudit(
  roomCode: string,
  hostToken: string,
): Promise<HostAuditExport> {
  return apiFetch(`${roomPath(roomCode)}/audit`, {
    headers: { Authorization: `Bearer ${hostToken}` },
  });
}
