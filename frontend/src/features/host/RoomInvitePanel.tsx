import { Check, Copy, ExternalLink, QrCode } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useRef, useState } from "react";

export interface RoomInvitePanelProps {
  roomCode: string;
}

export function RoomInvitePanel({ roomCode }: RoomInvitePanelProps) {
  const joinUrl = new URL(
    `/join/${encodeURIComponent(roomCode)}`,
    window.location.origin,
  ).toString();
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [qrError, setQrError] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const linkInputRef = useRef<HTMLInputElement | null>(null);
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let active = true;
    void QRCode.toDataURL(joinUrl, {
      errorCorrectionLevel: "M",
      margin: 1,
      width: 240,
    })
      .then((dataUrl) => {
        if (active) setQrDataUrl(dataUrl);
      })
      .catch(() => {
        if (active) setQrError(true);
      });
    return () => {
      active = false;
    };
  }, [joinUrl]);

  useEffect(
    () => () => {
      if (copiedTimerRef.current !== null) {
        globalThis.clearTimeout(copiedTimerRef.current);
      }
    },
    [],
  );

  async function handleCopy() {
    setCopyError(false);
    try {
      if (typeof navigator.clipboard?.writeText === "function") {
        try {
          await navigator.clipboard.writeText(joinUrl);
        } catch {
          copyWithSelection();
        }
      } else {
        copyWithSelection();
      }
      setCopied(true);
      if (copiedTimerRef.current !== null) {
        globalThis.clearTimeout(copiedTimerRef.current);
      }
      copiedTimerRef.current = globalThis.setTimeout(
        () => setCopied(false),
        2000,
      );
    } catch {
      setCopyError(true);
    }
  }

  function copyWithSelection() {
    linkInputRef.current?.select();
    if (!document.execCommand("copy")) {
      throw new Error("copy failed");
    }
  }

  return (
    <section
      aria-labelledby="room-invite-title"
      className="grid gap-4 rounded-lg bg-surface-raised p-5"
    >
      <div className="flex items-start gap-3">
        <QrCode aria-hidden="true" className="mt-1" size={21} />
        <div className="grid gap-1">
          <h2 id="room-invite-title" className="text-xl font-semibold">
            玩家邀请
          </h2>
          <p className="text-sm text-text-muted">
            扫码加入，或复制链接发给朋友。
          </p>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-[auto_minmax(0,1fr)] sm:items-center">
        {qrDataUrl === null ? (
          <div
            className="grid h-40 w-40 place-items-center rounded-md border border-text-muted/30 text-center text-xs text-text-muted"
            role="status"
          >
            {qrError ? "二维码生成失败" : "正在生成二维码"}
          </div>
        ) : (
          <img
            alt="玩家加入二维码"
            className="h-40 w-40 rounded-md bg-surface p-2"
            height={160}
            src={qrDataUrl}
            width={160}
          />
        )}

        <div className="grid min-w-0 gap-3">
          <div className="grid gap-1">
            <span className="text-xs font-medium text-text-muted">房间码</span>
            <strong className="font-mono text-2xl">{roomCode}</strong>
          </div>
          <label className="grid gap-2 text-sm font-medium">
            邀请链接
            <input
              aria-label="邀请链接"
              className="min-h-11 min-w-0 rounded-lg border border-text-muted/40 bg-surface px-3 font-mono text-sm text-text"
              onFocus={(event) => event.currentTarget.select()}
              readOnly
              ref={linkInputRef}
              value={joinUrl}
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <button
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-action px-4 font-semibold text-surface"
              onClick={() => void handleCopy()}
              type="button"
            >
              {copied ? (
                <Check aria-hidden="true" size={18} />
              ) : (
                <Copy aria-hidden="true" size={18} />
              )}
              {copied ? "已复制链接" : "复制邀请链接"}
            </button>
            <a
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-text-muted/40 px-4 font-semibold text-text"
              href={joinUrl}
              rel="noreferrer"
              target="_blank"
            >
              <ExternalLink aria-hidden="true" size={18} />
              打开加入页
            </a>
          </div>
          {copyError ? (
            <p className="text-sm text-danger" role="alert">
              复制失败，请手动复制链接。
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
