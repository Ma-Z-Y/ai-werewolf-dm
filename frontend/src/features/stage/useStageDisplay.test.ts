import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useStageDisplay } from "./useStageDisplay";

interface Sentinel {
  addEventListener: (type: "release", listener: () => void) => void;
  release: () => Promise<void>;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function wakeLockSentinel(onRelease?: (listener: () => void) => void): Sentinel {
  return {
    addEventListener: vi.fn((_type, listener) => {
      onRelease?.(listener);
    }),
    release: vi.fn().mockResolvedValue(undefined),
  };
}

const originalRequestFullscreen = Object.getOwnPropertyDescriptor(
  document.documentElement,
  "requestFullscreen",
);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (originalRequestFullscreen === undefined) {
    delete (document.documentElement as unknown as Record<string, unknown>)
      .requestFullscreen;
  } else {
    Object.defineProperty(
      document.documentElement,
      "requestFullscreen",
      originalRequestFullscreen,
    );
  }
});

describe("useStageDisplay", () => {
  it("starts local presentation controls inactive after reload", () => {
    const { result } = renderHook(() => useStageDisplay());

    expect(result.current.isFullscreen).toBe(false);
    expect(result.current.wakeLockActive).toBe(false);
    expect(result.current.wakeLockRequested).toBe(false);
  });

  it("reports unsupported presentation features as disabled", () => {
    const { result } = renderHook(() => useStageDisplay());

    expect(result.current.presentationStatus).toContain("当前浏览器不支持");
  });

  it("reports a rejected wake lock as retryable and does not enter active state", async () => {
    const request = vi.fn().mockRejectedValue(new Error("denied"));
    vi.stubGlobal("navigator", {
      ...globalThis.navigator,
      wakeLock: { request },
    });

    const { result } = renderHook(() => useStageDisplay());
    await act(async () => {
      await result.current.toggleWakeLock();
    });

    expect(result.current.wakeLockRequested).toBe(false);
    expect(result.current.wakeLockActive).toBe(false);
    expect(result.current.presentationStatus).toBe("屏幕常亮请求失败，可重试");
  });

  it("reports a rejected fullscreen request as retryable", async () => {
    const requestFullscreen = vi.fn().mockRejectedValue(new Error("blocked"));
    Object.defineProperty(document.documentElement, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });

    const { result } = renderHook(() => useStageDisplay());
    await act(async () => {
      await result.current.toggleFullscreen();
    });

    expect(requestFullscreen).toHaveBeenCalledTimes(1);
    expect(result.current.presentationStatus).toBe("全屏请求失败，可重试");
  });

  it("reacquires a requested wake lock when the page becomes visible again", async () => {
    let releaseListener: (() => void) | undefined;
    const firstSentinel = wakeLockSentinel((listener) => {
      releaseListener = listener;
    });
    const secondSentinel = wakeLockSentinel();
    const request = vi
      .fn()
      .mockResolvedValueOnce(firstSentinel)
      .mockResolvedValueOnce(secondSentinel);
    vi.stubGlobal("navigator", {
      ...globalThis.navigator,
      wakeLock: { request },
    });

    const { result } = renderHook(() => useStageDisplay());
    await act(async () => {
      await result.current.toggleWakeLock();
    });
    expect(result.current.wakeLockActive).toBe(true);

    await act(async () => {
      releaseListener?.();
    });
    expect(result.current.wakeLockActive).toBe(false);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => {
      expect(request).toHaveBeenCalledTimes(2);
      expect(result.current.wakeLockActive).toBe(true);
    });
  });

  it("ignores a late rejection from a superseded wake lock request", async () => {
    const firstRequest = deferred<Sentinel>();
    const secondSentinel = wakeLockSentinel();
    const request = vi
      .fn()
      .mockImplementationOnce(() => firstRequest.promise)
      .mockResolvedValueOnce(secondSentinel);
    vi.stubGlobal("navigator", {
      ...globalThis.navigator,
      wakeLock: { request },
    });

    const { result } = renderHook(() => useStageDisplay());
    act(() => {
      void result.current.toggleWakeLock();
    });
    await waitFor(() => expect(result.current.wakeLockRequested).toBe(true));

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => {
      expect(request).toHaveBeenCalledTimes(2);
      expect(result.current.wakeLockActive).toBe(true);
    });

    await act(async () => {
      firstRequest.reject(new Error("stale request failed"));
      await Promise.resolve();
    });

    expect(result.current.wakeLockRequested).toBe(true);
    expect(result.current.wakeLockActive).toBe(true);
    expect(result.current.presentationStatus).not.toContain(
      "屏幕常亮请求失败",
    );
  });

  it("handles a rejected release while unmounting", async () => {
    const catchRelease = vi.fn();
    const sentinel = wakeLockSentinel();
    sentinel.release = vi
      .fn()
      .mockReturnValue({ catch: catchRelease } as unknown as Promise<void>);
    const request = vi.fn().mockResolvedValue(sentinel);
    vi.stubGlobal("navigator", {
      ...globalThis.navigator,
      wakeLock: { request },
    });

    const { result, unmount } = renderHook(() => useStageDisplay());
    await act(async () => {
      await result.current.toggleWakeLock();
    });
    unmount();

    expect(catchRelease).toHaveBeenCalledTimes(1);
  });
});
