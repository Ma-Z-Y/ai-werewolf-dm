import { Download, Wifi, WifiOff } from "lucide-react";

import type { HostControlView } from "../../protocol/models";
import type { RoomSocketState } from "../../realtime/RoomSocket";

export interface HostDiagnosticsProps {
  roomCode: string;
  connectionState: RoomSocketState;
  hostControl: HostControlView;
  auditPending: boolean;
  onDownloadAudit: () => void;
}

const CONNECTION_LABELS: Record<RoomSocketState, string> = {
  idle: "等待连接",
  connecting: "连接中",
  awaiting_auth: "正在认证",
  ready: "已连接",
  retry_wait: "连接中断，正在重连",
  closed: "连接已关闭",
};

export function HostDiagnostics({
  roomCode,
  connectionState,
  hostControl,
  auditPending,
  onDownloadAudit,
}: HostDiagnosticsProps) {
  const connected = connectionState === "ready";
  const ConnectionIcon = connected ? Wifi : WifiOff;

  return (
    <section aria-labelledby="host-diagnostics-title" className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="host-diagnostics-title" className="text-lg font-semibold">
          运行诊断
        </h2>
        <span
          className={`inline-flex items-center gap-2 text-sm ${
            connected ? "text-success" : "text-text-muted"
          }`}
          role="status"
        >
          <ConnectionIcon aria-hidden="true" size={17} />
          {CONNECTION_LABELS[connectionState]}
        </span>
      </div>

      <dl className="grid gap-3 sm:grid-cols-3">
        <div className="grid gap-1">
          <dt className="text-xs font-medium uppercase text-text-muted">
            房间
          </dt>
          <dd className="font-mono text-base font-semibold">{roomCode}</dd>
        </div>
        <div className="grid gap-1">
          <dt className="text-xs font-medium uppercase text-text-muted">
            状态版本
          </dt>
          <dd
            className="font-mono text-base font-semibold"
            data-testid="host-revision"
          >
            {hostControl.revision}
          </dd>
        </div>
        <div className="grid gap-1">
          <dt className="text-xs font-medium uppercase text-text-muted">
            游戏状态
          </dt>
          <dd className="text-base font-semibold">
            {hostControl.paused ? "已暂停" : "进行中"}
          </dd>
        </div>
      </dl>

      <button
        className="inline-flex min-h-11 w-fit items-center justify-center gap-2 rounded-lg border border-text-muted/40 px-4 font-semibold text-text disabled:cursor-not-allowed disabled:opacity-50"
        disabled={auditPending}
        onClick={onDownloadAudit}
        type="button"
      >
        <Download aria-hidden="true" size={18} />
        {auditPending ? "正在生成审计" : "下载主持人审计"}
      </button>
    </section>
  );
}
