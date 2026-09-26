import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { AppError } from "../../protocol/errors";
import type { PlayerReplay } from "../../protocol/models";
import { getPlayerReplay } from "../../session/roomSession";
import {
  readSeatSession,
  type StoredSeatSession,
} from "../../session/storage";

import { ReplayTimeline } from "./ReplayTimeline";

const SEAT_SESSION_KEY_PREFIX = "werewolf:v1:room";

function normalizeRoomCode(value: string | undefined): string {
  return value?.trim().toUpperCase() ?? "";
}

function clearStoredSeatSession(roomCode: string): void {
  try {
    globalThis.localStorage.removeItem(
      `${SEAT_SESSION_KEY_PREFIX}:${roomCode}:seat`,
    );
  } catch {
    // Storage can be unavailable or read-only; the replay route still exits.
  }
}

function replayErrorMessage(error: unknown): string {
  return error instanceof AppError
    ? error.message
    : "回放加载失败，请重试";
}

interface ReplayResult {
  key: string;
  replay: PlayerReplay | null;
  error: string | null;
}

export function ReplayRoute() {
  const { roomCode: routeCode } = useParams();
  const location = useLocation();
  const roomCode = normalizeRoomCode(routeCode);
  const revisionHint = new URLSearchParams(location.search).get("revision");
  const [session, setSession] = useState<StoredSeatSession | null>(() =>
    roomCode.length > 0 ? readSeatSession(roomCode) : null,
  );
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const replayRequestRef = useRef<{
    key: string;
    promise: Promise<PlayerReplay>;
  } | null>(null);
  const requestKey =
    session === null
      ? null
      : `${session.roomCode}:${session.token}:${revisionHint ?? ""}`;
  const loading = requestKey !== null && result?.key !== requestKey;
  const replay = result?.key === requestKey ? result.replay : null;
  const error =
    session === null
      ? sessionError
      : result?.key === requestKey
        ? result.error
        : null;

  useEffect(() => {
    if (session === null || requestKey === null) return;

    let cancelled = false;
    if (replayRequestRef.current?.key !== requestKey) {
      replayRequestRef.current = {
        key: requestKey,
        promise: getPlayerReplay(session.roomCode, session.token),
      };
    }

    void replayRequestRef.current.promise
      .then((loadedReplay) => {
        if (!cancelled) {
          setResult({
            key: requestKey,
            replay: loadedReplay,
            error: null,
          });
        }
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        if (
          caught instanceof AppError &&
          (caught.code === "TOKEN_EXPIRED" || caught.code === "TOKEN_INVALID")
        ) {
          clearStoredSeatSession(session.roomCode);
          setSessionError("登录已过期，请重新加入。");
          setSession(null);
          return;
        }
        setResult({
          key: requestKey,
          replay: null,
          error: replayErrorMessage(caught),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [requestKey, session]);

  if (session === null) {
    return (
      <main className="grid min-h-screen place-items-center bg-surface px-6 py-12 text-text">
        <section className="grid max-w-md gap-4 text-center">
          <h1 className="text-3xl font-semibold">玩家回放</h1>
          <p className="text-sm text-danger" role="alert">
            {error ?? "未找到有效玩家会话，请先重新加入房间。"}
          </p>
          <Link
            className="inline-flex min-h-11 items-center justify-center rounded-lg bg-action px-4 font-semibold text-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
            to={`/join/${encodeURIComponent(roomCode)}`}
          >
            返回加入房间
          </Link>
        </section>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="grid min-h-screen place-items-center bg-surface px-6 py-12 text-text">
        <p className="text-sm text-text-muted" role="status">
          正在加载玩家回放
        </p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-surface px-5 py-8 text-text sm:px-8">
      <div className="mx-auto grid w-full max-w-2xl gap-6">
        <header className="grid gap-2">
          <p className="text-sm font-medium text-text-muted">
            {`房间 ${roomCode}`}
          </p>
          <h1 className="text-3xl font-semibold">玩家回放</h1>
          <p className="text-sm text-text-muted">
            {`状态版本 ${replay?.revision ?? 0}`}
          </p>
        </header>

        {error === null ? (
          <ReplayTimeline
            privateFacts={replay?.private_facts ?? []}
            publicTimeline={replay?.public_timeline ?? []}
          />
        ) : (
          <p className="text-sm text-danger" role="alert">
            {error}
          </p>
        )}

        <Link
          className="inline-flex min-h-11 items-center justify-center rounded-lg border border-text-muted/40 px-4 font-semibold text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          to={`/play/${encodeURIComponent(roomCode)}`}
        >
          返回玩家席
        </Link>
      </div>
    </main>
  );
}
