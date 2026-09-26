import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

interface WakeLockSentinelLike {
  release: () => Promise<void>;
  addEventListener: (type: "release", listener: () => void) => void;
}

interface WakeLockLike {
  request: (type: "screen") => Promise<WakeLockSentinelLike>;
}

export interface StageDisplayControls {
  fullscreenSupported: boolean;
  isFullscreen: boolean;
  presentationStatus: string | null;
  toggleFullscreen: () => Promise<void>;
  toggleWakeLock: () => Promise<void>;
  wakeLockActive: boolean;
  wakeLockRequested: boolean;
  wakeLockSupported: boolean;
}

function wakeLockApi(): WakeLockLike | null {
  const navigatorWithWakeLock = globalThis.navigator as
    | (Navigator & { wakeLock?: WakeLockLike })
    | undefined;
  return navigatorWithWakeLock?.wakeLock ?? null;
}

function fullscreenSupported(): boolean {
  return (
    typeof document !== "undefined" &&
    typeof document.documentElement.requestFullscreen === "function"
  );
}

function subscribeFullscreen(listener: () => void): () => void {
  document.addEventListener("fullscreenchange", listener);
  return () => document.removeEventListener("fullscreenchange", listener);
}

function getFullscreenSnapshot(): boolean {
  return document.fullscreenElement != null;
}

function getFullscreenServerSnapshot(): boolean {
  return false;
}

export function useStageDisplay(): StageDisplayControls {
  const [wakeLockRequested, setWakeLockRequested] = useState(false);
  const [wakeLockActive, setWakeLockActive] = useState(false);
  const [failureStatus, setFailureStatus] = useState<string | null>(null);
  const wakeLockSentinelRef = useRef<WakeLockSentinelLike | null>(null);
  const wakeLockGenerationRef = useRef(0);
  const mountedRef = useRef(false);
  const isFullscreen = useSyncExternalStore(
    subscribeFullscreen,
    getFullscreenSnapshot,
    getFullscreenServerSnapshot,
  );
  const canUseFullscreen = fullscreenSupported();
  const canUseWakeLock = wakeLockApi() !== null;
  const unsupportedStatus = [
    canUseFullscreen ? null : "全屏",
    canUseWakeLock ? null : "屏幕常亮",
  ].filter((feature): feature is string => feature !== null);
  const presentationStatus =
    failureStatus ??
    (unsupportedStatus.length === 0
      ? null
      : `当前浏览器不支持${unsupportedStatus.join("和")}`);

  const requestWakeLock = useCallback(async () => {
    const api = wakeLockApi();
    if (api === null) return;
    const generation = wakeLockGenerationRef.current + 1;
    wakeLockGenerationRef.current = generation;
    try {
      const sentinel = await api.request("screen");
      if (
        !mountedRef.current ||
        generation !== wakeLockGenerationRef.current
      ) {
        await sentinel.release();
        return;
      }
      const previousSentinel = wakeLockSentinelRef.current;
      wakeLockSentinelRef.current = sentinel;
      sentinel.addEventListener("release", () => {
        if (wakeLockSentinelRef.current !== sentinel) return;
        wakeLockSentinelRef.current = null;
        if (mountedRef.current) setWakeLockActive(false);
      });
      setFailureStatus(null);
      setWakeLockActive(true);
      if (previousSentinel !== null && previousSentinel !== sentinel) {
        try {
          await previousSentinel.release();
        } catch {
          // The new sentinel remains authoritative if old cleanup fails.
        }
      }
    } catch {
      if (
        mountedRef.current &&
        generation === wakeLockGenerationRef.current
      ) {
        wakeLockSentinelRef.current = null;
        setWakeLockActive(false);
        setWakeLockRequested(false);
        setFailureStatus("屏幕常亮请求失败，可重试");
      }
    }
  }, []);

  const releaseWakeLock = useCallback(async () => {
    wakeLockGenerationRef.current += 1;
    const sentinel = wakeLockSentinelRef.current;
    wakeLockSentinelRef.current = null;
    setWakeLockActive(false);
    if (sentinel !== null) {
      try {
        await sentinel.release();
      } catch {
        // Presentation-only lock; policy failures are non-fatal.
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      wakeLockGenerationRef.current += 1;
      const sentinel = wakeLockSentinelRef.current;
      wakeLockSentinelRef.current = null;
      if (sentinel !== null) {
        void sentinel.release().catch(() => {
          // Best-effort cleanup during unmount.
        });
      }
    };
  }, []);

  useEffect(() => {
    function handleVisibilityChange() {
      if (
        document.visibilityState === "visible" &&
        wakeLockRequested &&
        wakeLockSentinelRef.current === null
      ) {
        void requestWakeLock();
      }
    }

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [requestWakeLock, wakeLockRequested]);

  const toggleFullscreen = useCallback(async () => {
    if (!canUseFullscreen) return;
    try {
      if (document.fullscreenElement == null) {
        await document.documentElement.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
      setFailureStatus(null);
    } catch {
      setFailureStatus("全屏请求失败，可重试");
    }
  }, [canUseFullscreen]);

  const toggleWakeLock = useCallback(async () => {
    if (!canUseWakeLock) return;
    if (wakeLockRequested) {
      setWakeLockRequested(false);
      setFailureStatus(null);
      await releaseWakeLock();
      return;
    }
    setWakeLockRequested(true);
    setFailureStatus(null);
    await requestWakeLock();
  }, [canUseWakeLock, releaseWakeLock, requestWakeLock, wakeLockRequested]);

  return {
    fullscreenSupported: canUseFullscreen,
    isFullscreen,
    presentationStatus,
    toggleFullscreen,
    toggleWakeLock,
    wakeLockActive,
    wakeLockRequested,
    wakeLockSupported: canUseWakeLock,
  };
}
