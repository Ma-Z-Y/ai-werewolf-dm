import { MonitorSmartphone } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AppError } from "../../protocol/errors";
import { exchangeDisplayPairing } from "../../session/roomSession";
import {
  writeDisplaySession,
  type StoredDisplaySession,
} from "../../session/storage";

export interface StagePairingScreenProps {
  roomCode: string;
  sessionError?: string | null;
  onPaired: (session: StoredDisplaySession) => void;
}

function normalizePairingCode(value: string): string {
  return value.replace(/\D/g, "").slice(0, 6);
}

function pairingErrorMessage(error: unknown): string {
  if (
    error instanceof AppError &&
    (error.code === "TOKEN_INVALID" || error.code === "TOKEN_EXPIRED")
  ) {
    return "配对码无效或已过期，请重新生成";
  }
  if (error instanceof AppError && error.code === "RATE_LIMITED") {
    return "尝试次数过多，请重新生成配对码";
  }
  return error instanceof AppError ? error.message : "发生未知错误";
}

export function StagePairingScreen({
  roomCode,
  sessionError = null,
  onPaired,
}: StagePairingScreenProps) {
  const [pairingCode, setPairingCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const canSubmit = roomCode.length > 0 && pairingCode.length === 6 && !submitting;
  const visibleError = errorMessage ?? sessionError;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setErrorMessage(null);
    try {
      const display = await exchangeDisplayPairing(roomCode, pairingCode);
      const session: StoredDisplaySession = {
        schemaVersion: 1,
        roomCode,
        roomId: display.room_id,
        token: display.display_token,
        expiresAt: display.expires_at,
      };
      writeDisplaySession(session);
      setPairingCode("");
      onPaired(session);
    } catch (error) {
      setErrorMessage(pairingErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-phase-night px-5 py-10 text-text">
      <section className="grid w-full max-w-lg gap-7">
        <header className="grid gap-3">
          <p className="text-sm font-medium text-text-muted">
            {`房间码 ${roomCode}`}
          </p>
          <h1 className="text-4xl font-semibold">共享屏配对</h1>
          <p className="max-w-md text-base text-text-muted">
            输入主持人设备上显示的六位配对码。
          </p>
        </header>

        <form className="grid gap-5" onSubmit={handleSubmit}>
          <label className="grid gap-2 text-sm font-medium">
            配对码
            <input
              autoComplete="one-time-code"
              className="min-h-14 rounded-lg border border-text-muted/50 bg-surface px-4 text-center font-mono text-3xl tracking-[0.35em] text-text outline-none focus:border-action"
              inputMode="numeric"
              maxLength={6}
              pattern="[0-9]{6}"
              onChange={(event) =>
                setPairingCode(normalizePairingCode(event.target.value))
              }
              value={pairingCode}
            />
          </label>

          {visibleError === null ? null : (
            <p className="text-sm text-danger" role="alert">
              {visibleError}
            </p>
          )}

          <button
            className="inline-flex min-h-12 items-center justify-center gap-3 rounded-lg bg-action px-5 text-base font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!canSubmit}
            type="submit"
          >
            <MonitorSmartphone aria-hidden="true" size={20} />
            {submitting ? "正在配对" : "连接共享屏"}
          </button>
        </form>
      </section>
    </main>
  );
}
