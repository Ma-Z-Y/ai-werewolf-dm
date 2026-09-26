import {
  KeyRound,
  Pause,
  Play,
  ShieldAlert,
  UsersRound,
} from "lucide-react";

import type { HostControlView } from "../../protocol/models";
import type { RoomSocketState } from "../../realtime/RoomSocket";

import { HostCorrectionNotice } from "./HostCorrectionNotice";
import { HostDiagnostics } from "./HostDiagnostics";

export interface HostControlScreenProps {
  roomCode: string;
  connectionState: RoomSocketState;
  hostControl: HostControlView;
  errorMessage: string | null;
  pausePending: boolean;
  resumePending: boolean;
  pairingCode: string | null;
  pairingExpired: boolean;
  pairingPending: boolean;
  revokePending: boolean;
  auditPending: boolean;
  onPause: () => void;
  onResume: () => void;
  onGeneratePairing: () => void;
  onRevokeDisplay: () => void;
  onDownloadAudit: () => void;
}

export function HostControlScreen({
  roomCode,
  connectionState,
  hostControl,
  errorMessage,
  pausePending,
  resumePending,
  pairingCode,
  pairingExpired,
  pairingPending,
  revokePending,
  auditPending,
  onPause,
  onResume,
  onGeneratePairing,
  onRevokeDisplay,
  onDownloadAudit,
}: HostControlScreenProps) {
  const commandPending = pausePending || resumePending;

  return (
    <main className="min-h-screen bg-surface px-5 py-8 text-text sm:px-8">
      <div className="mx-auto grid w-full max-w-4xl gap-8">
        <header className="grid gap-2 border-b border-text-muted/30 pb-5">
          <p className="text-sm font-medium text-text-muted">{`房间 ${roomCode}`}</p>
          <h1 className="text-3xl font-semibold">主持人控制台</h1>
          <p className="text-sm text-text-muted">
            {hostControl.paused ? "游戏已暂停" : "游戏进行中"}
          </p>
        </header>

        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="grid content-start gap-8">
            <section aria-labelledby="host-round-control-title" className="grid gap-4">
              <div className="flex items-center justify-between gap-4">
                <h2
                  className="inline-flex items-center gap-2 text-xl font-semibold"
                  id="host-round-control-title"
                >
                  <UsersRound aria-hidden="true" size={21} />
                  局面控制
                </h2>
                <span className="text-sm text-text-muted">
                  {`${hostControl.public_view.living_seats.length} 人存活`}
                </span>
              </div>

              {hostControl.paused ? (
                <button
                  className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-action px-5 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={commandPending}
                  onClick={onResume}
                  type="button"
                >
                  <Play aria-hidden="true" size={20} />
                  {resumePending ? "正在恢复" : "恢复游戏"}
                </button>
              ) : (
                <button
                  className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-action px-5 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={commandPending}
                  onClick={onPause}
                  type="button"
                >
                  <Pause aria-hidden="true" size={20} />
                  {pausePending ? "正在暂停" : "暂停游戏"}
                </button>
              )}
            </section>

            <section aria-labelledby="host-display-title" className="grid gap-4">
              <div className="flex items-start gap-3">
                <KeyRound aria-hidden="true" className="mt-1" size={21} />
                <div className="grid gap-1">
                  <h2 id="host-display-title" className="text-xl font-semibold">
                    共享屏
                  </h2>
                  <p className="text-sm text-text-muted">
                    配对码仅用于当前房间，生成后请及时在共享屏输入。
                  </p>
                </div>
              </div>

              {pairingCode === null ? (
                <p className="text-sm text-text-muted">
                  {pairingExpired
                    ? "配对码已失效，请重新生成。"
                    : "当前没有待使用的配对码"}
                </p>
              ) : (
                <div
                  aria-live="polite"
                  className="grid gap-1 rounded-md border border-action/50 bg-action/10 px-5 py-4"
                >
                  <span className="text-xs font-medium text-text-muted">
                    当前配对码
                  </span>
                  <strong className="font-mono text-3xl">
                    {pairingCode}
                  </strong>
                </div>
              )}

              <div className="flex flex-wrap gap-3">
                <button
                  className="inline-flex min-h-11 items-center justify-center rounded-lg bg-action px-4 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={pairingPending}
                  onClick={onGeneratePairing}
                  type="button"
                >
                  {pairingPending ? "正在生成" : "生成共享屏配对码"}
                </button>
                <button
                  className="inline-flex min-h-11 items-center justify-center rounded-lg border border-danger/60 px-4 font-semibold text-danger disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={revokePending}
                  onClick={onRevokeDisplay}
                  type="button"
                >
                  {revokePending ? "正在撤销" : "撤销共享屏"}
                </button>
              </div>
            </section>

            <HostCorrectionNotice />
          </div>

          <aside className="grid content-start gap-6 border-text-muted/30 lg:border-l lg:pl-8">
            <div className="flex items-start gap-3">
              <ShieldAlert aria-hidden="true" className="mt-1" size={21} />
              <div className="grid gap-1">
                <h2 className="text-lg font-semibold">主持人记录</h2>
                <p className="text-sm text-text-muted">
                  下载当前房间的完整主持人审计记录。
                </p>
              </div>
            </div>
            <HostDiagnostics
              auditPending={auditPending}
              connectionState={connectionState}
              hostControl={hostControl}
              onDownloadAudit={onDownloadAudit}
              roomCode={roomCode}
            />
          </aside>
        </div>

        {errorMessage === null ? null : (
          <p className="text-sm text-danger" role="alert">
            {errorMessage}
          </p>
        )}
      </div>
    </main>
  );
}
