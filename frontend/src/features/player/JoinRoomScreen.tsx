import { LogIn } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AppError } from "../../protocol/errors";
import { joinRoom } from "../../session/roomSession";
import { writeSeatSession, type StoredSeatSession } from "../../session/storage";

export interface JoinRoomScreenProps {
  initialRoomCode?: string;
  expiredSession?: boolean;
  onJoined: (session: StoredSeatSession, displayName: string) => void;
}

function normalizeRoomCode(value: string): string {
  return value.trim().toUpperCase().replace(/\s+/g, "").slice(0, 12);
}

function isValidDisplayName(value: string): boolean {
  const length = value.trim().length;
  return length >= 1 && length <= 24;
}

export function JoinRoomScreen({
  initialRoomCode = "",
  expiredSession = false,
  onJoined,
}: JoinRoomScreenProps) {
  const [roomCode, setRoomCode] = useState(normalizeRoomCode(initialRoomCode));
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const canSubmit =
    roomCode.length > 0 && isValidDisplayName(displayName) && !submitting;

  async function handleJoin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setErrorMessage(null);
    try {
      const joined = await joinRoom(roomCode, displayName);
      const session: StoredSeatSession = {
        schemaVersion: 1,
        roomCode,
        roomId: joined.room_id,
        seatId: joined.seat_id,
        token: joined.seat_token,
        expiresAt: joined.expires_at,
      };
      writeSeatSession(session);
      onJoined(session, displayName.trim());
    } catch (error) {
      setErrorMessage(
        error instanceof AppError ? error.message : "发生未知错误",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-surface px-5 py-8 text-text sm:px-8">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-md flex-col justify-center gap-6">
        <header className="grid gap-2">
          <p className="text-sm font-medium text-text-muted">玩家入口</p>
          <h1 className="text-3xl font-semibold">加入房间</h1>
        </header>

        {expiredSession ? (
          <p className="text-sm text-danger" role="alert">
            会话已过期，请重新加入
          </p>
        ) : null}

        <form
          className="grid gap-5 rounded-lg bg-surface-raised p-5"
          onSubmit={handleJoin}
        >
          <label className="grid gap-2 text-sm font-medium">
            房间码
            <input
              autoCapitalize="characters"
              autoComplete="off"
              className="min-h-11 rounded-lg border border-text-muted/40 bg-surface px-3 text-base uppercase text-text outline-none focus:border-action"
              maxLength={12}
              onChange={(event) =>
                setRoomCode(normalizeRoomCode(event.target.value))
              }
              value={roomCode}
            />
          </label>

          <label className="grid gap-2 text-sm font-medium">
            名字
            <input
              autoComplete="name"
              className="min-h-11 rounded-lg border border-text-muted/40 bg-surface px-3 text-base text-text outline-none focus:border-action"
              onChange={(event) => setDisplayName(event.target.value)}
              value={displayName}
            />
          </label>

          {errorMessage === null ? null : (
            <p className="text-sm text-danger" role="alert">
              {errorMessage}
            </p>
          )}

          <button
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-action px-4 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!canSubmit}
            type="submit"
          >
            <LogIn aria-hidden="true" size={18} />
            加入房间
          </button>
        </form>
      </div>
    </main>
  );
}
