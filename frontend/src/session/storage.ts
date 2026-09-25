export interface StoredSeatSession {
  schemaVersion: 1;
  roomCode: string;
  roomId: string;
  seatId: number;
  token: string;
  expiresAt: string;
}

export interface StoredHostSession {
  schemaVersion: 1;
  roomCode: string;
  roomId: string;
  token: string;
  expiresAt: string;
}

export interface StoredDisplaySession {
  schemaVersion: 1;
  roomCode: string;
  roomId: string;
  token: string;
  expiresAt: string;
}

const ROOM_PREFIX = "werewolf:v1:room";
const LAST_HOST_ROOM_KEY = "werewolf:v1:last-host-room";

function normalizeRoomCode(roomCode: string): string {
  return roomCode.trim().toUpperCase();
}

function sessionKey(roomCode: string, role: "seat" | "host" | "display") {
  return `${ROOM_PREFIX}:${normalizeRoomCode(roomCode)}:${role}`;
}

function getStorage(kind: "local" | "session"): Storage | null {
  try {
    return kind === "local" ? globalThis.localStorage : globalThis.sessionStorage;
  } catch {
    return null;
  }
}

function safeRemove(storage: Storage | null, key: string): void {
  try {
    storage?.removeItem(key);
  } catch {
    // Storage can be unavailable or read-only; game logic must continue.
  }
}

function removeOtherRoomSessions(
  storage: Storage | null,
  roomCode: string,
  role: "seat" | "host" | "display",
): void {
  if (storage === null) return;
  const currentKey = sessionKey(roomCode, role);
  try {
    const keys = Array.from(
      { length: storage.length },
      (_, index) => storage.key(index),
    );
    for (const key of keys) {
      if (
        key !== null &&
        key.startsWith(`${ROOM_PREFIX}:`) &&
        key.endsWith(`:${role}`) &&
        key !== currentKey
      ) {
        safeRemove(storage, key);
      }
    }
  } catch {
    // Storage can be unavailable or read-only; game logic must continue.
  }
}

function readJson(storage: Storage | null, key: string): unknown | null {
  if (storage === null) return null;
  try {
    const raw = storage.getItem(key);
    if (raw === null) return null;
    return JSON.parse(raw) as unknown;
  } catch {
    safeRemove(storage, key);
    return null;
  }
}

const ISO_UTC_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;

function isValidExpiresAt(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = ISO_UTC_PATTERN.exec(value);
  if (match === null) return false;

  const [, year, month, day, hour, minute, second, fraction = "0"] = match;
  const parsed = new Date(value);
  const parsedTimestamp = parsed.getTime();
  if (!Number.isFinite(parsedTimestamp) || parsedTimestamp <= Date.now()) {
    return false;
  }

  return (
    parsed.getUTCFullYear() === Number(year) &&
    parsed.getUTCMonth() + 1 === Number(month) &&
    parsed.getUTCDate() === Number(day) &&
    parsed.getUTCHours() === Number(hour) &&
    parsed.getUTCMinutes() === Number(minute) &&
    parsed.getUTCSeconds() === Number(second) &&
    parsed.getUTCMilliseconds() ===
      Number(fraction.padEnd(3, "0").slice(0, 3))
  );
}

function hasValidBase(
  value: unknown,
  roomCode: string,
): value is {
  schemaVersion: 1;
  roomCode: string;
  roomId: string;
  token: string;
  expiresAt: string;
} {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    record.schemaVersion === 1 &&
    record.roomCode === normalizeRoomCode(roomCode) &&
    typeof record.roomId === "string" &&
    record.roomId.length > 0 &&
    typeof record.token === "string" &&
    record.token.length > 0 &&
    isValidExpiresAt(record.expiresAt)
  );
}

function isSeatSession(
  value: unknown,
  roomCode: string,
): value is StoredSeatSession {
  if (!hasValidBase(value, roomCode)) return false;
  return (
    Number.isInteger((value as Record<string, unknown>).seatId) &&
    ((value as Record<string, unknown>).seatId as number) >= 1 &&
    ((value as Record<string, unknown>).seatId as number) <= 6
  );
}

function isHostSession(
  value: unknown,
  roomCode: string,
): value is StoredHostSession {
  return hasValidBase(value, roomCode);
}

function isDisplaySession(
  value: unknown,
  roomCode: string,
): value is StoredDisplaySession {
  return hasValidBase(value, roomCode);
}

function readSession<T>(
  storageKind: "local" | "session",
  roomCode: string,
  role: "seat" | "host" | "display",
  validate: (value: unknown, roomCode: string) => value is T,
): T | null {
  const storage = getStorage(storageKind);
  removeOtherRoomSessions(storage, roomCode, role);
  const key = sessionKey(roomCode, role);
  const value = readJson(storage, key);
  if (value === null || !validate(value, roomCode)) {
    safeRemove(storage, key);
    return null;
  }
  return value;
}

function writeSession(
  storageKind: "local" | "session",
  roomCode: string,
  role: "seat" | "host" | "display",
  value: unknown,
): void {
  const storage = getStorage(storageKind);
  try {
    storage?.setItem(
      sessionKey(roomCode, role),
      JSON.stringify({ ...(value as Record<string, unknown>), roomCode: normalizeRoomCode(roomCode) }),
    );
  } catch {
    // Private browsing and quota failures are not gameplay errors.
  }
}

export function readSeatSession(roomCode: string): StoredSeatSession | null {
  return readSession("local", roomCode, "seat", isSeatSession);
}

export function writeSeatSession(session: StoredSeatSession): void {
  writeSession("local", session.roomCode, "seat", session);
}

export function readHostSession(roomCode: string): StoredHostSession | null {
  const storage = getStorage("local");
  const normalizedRoomCode = normalizeRoomCode(roomCode);
  removeOtherRoomSessions(storage, normalizedRoomCode, "host");
  const value = readJson(storage, sessionKey(normalizedRoomCode, "host"));
  if (value === null || !isHostSession(value, normalizedRoomCode)) {
    safeRemove(storage, sessionKey(normalizedRoomCode, "host"));
    try {
      if (storage?.getItem(LAST_HOST_ROOM_KEY) === normalizedRoomCode) {
        safeRemove(storage, LAST_HOST_ROOM_KEY);
      }
    } catch {
      // Storage can be unavailable or read-only; game logic must continue.
    }
    return null;
  }
  return value;
}

export function writeHostSession(session: StoredHostSession): void {
  const normalizedRoomCode = normalizeRoomCode(session.roomCode);
  writeSession("local", normalizedRoomCode, "host", session);
  const storage = getStorage("local");
  try {
    storage?.setItem(LAST_HOST_ROOM_KEY, normalizedRoomCode);
  } catch {
    // Private browsing and quota failures are not gameplay errors.
  }
}

export function readDisplaySession(
  roomCode: string,
): StoredDisplaySession | null {
  return readSession("session", roomCode, "display", isDisplaySession);
}

export function writeDisplaySession(session: StoredDisplaySession): void {
  writeSession("session", session.roomCode, "display", session);
}
