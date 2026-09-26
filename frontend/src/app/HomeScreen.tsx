import { DoorOpen, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AppError } from "../protocol/errors";
import { createRoom } from "../session/roomSession";
import { writeHostSession } from "../session/storage";

function isValidDisplayName(value: string): boolean {
  const length = value.trim().length;
  return length >= 1 && length <= 24;
}

export function HomeScreen() {
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const canSubmit = isValidDisplayName(displayName) && !submitting;

  async function handleCreateRoom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setErrorMessage(null);
    try {
      const room = await createRoom(displayName);
      writeHostSession({
        schemaVersion: 1,
        roomCode: room.room_code,
        roomId: room.room_id,
        token: room.host_token,
        expiresAt: room.expires_at,
      });
      navigate(`/host/${encodeURIComponent(room.room_code)}`);
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
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-2xl flex-col justify-center gap-8">
        <header className="grid gap-3">
          <p className="text-sm font-medium text-text-muted">
            AI 主持 · 真人玩家
          </p>
          <h1 className="text-4xl font-semibold tracking-normal sm:text-5xl">
            狼人杀 DM
          </h1>
          <p className="max-w-xl text-base leading-7 text-text-muted">
            创建一局，把房间码交给朋友；每个人用自己的手机加入。
          </p>
        </header>

        <section
          aria-labelledby="create-room-title"
          className="grid gap-5 rounded-lg bg-surface-raised p-5 sm:p-6"
        >
          <div className="grid gap-1">
            <h2 id="create-room-title" className="text-xl font-semibold">
              创建房间
            </h2>
            <p className="text-sm text-text-muted">
              主持人使用自己的手机或平板。
            </p>
          </div>

          <form className="grid gap-4" onSubmit={handleCreateRoom}>
            <label className="grid gap-2 text-sm font-medium">
              主持人名字
              <input
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
              <Plus aria-hidden="true" size={18} />
              创建房间
            </button>
          </form>
        </section>

        <section className="grid gap-4 border-t border-text-muted/25 pt-6">
          <div className="grid gap-1">
            <h2 className="text-lg font-semibold">加入已有房间</h2>
            <p className="text-sm text-text-muted">
              输入主持人分享的六位房间码。
            </p>
          </div>
          <Link
            className="inline-flex min-h-11 w-fit items-center justify-center gap-2 rounded-lg border border-action px-4 font-semibold text-action"
            to="/join"
          >
            <DoorOpen aria-hidden="true" size={18} />
            加入房间
          </Link>
        </section>
      </div>
    </main>
  );
}
