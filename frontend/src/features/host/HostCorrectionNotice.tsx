export interface HostCorrectionNoticeProps {
  paused: boolean;
  pending: boolean;
}

export function HostCorrectionNotice({
  paused,
  pending,
}: HostCorrectionNoticeProps) {
  const message = pending
    ? "正在提交主持人纠错。"
    : paused
      ? null
      : "请先暂停游戏，再进行主持人纠错";
  if (message === null) return null;
  return (
    <p aria-live="polite" className="text-sm text-text-muted" role="status">
      {message}
    </p>
  );
}
