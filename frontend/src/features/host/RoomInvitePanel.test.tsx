import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RoomInvitePanel } from "./RoomInvitePanel";

vi.mock("qrcode", () => ({
  default: {
    toDataURL: vi.fn().mockResolvedValue("data:image/png;base64,qr"),
  },
}));

const writeText = vi.fn<(text: string) => Promise<void>>();
const execCommand = vi.fn(() => true);

describe("RoomInvitePanel", () => {
  beforeEach(() => {
    writeText.mockReset();
    writeText.mockResolvedValue();
    execCommand.mockReset();
    execCommand.mockReturnValue(true);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });
  });

  it("shows a shareable join URL and QR code", async () => {
    render(<RoomInvitePanel roomCode="ABCDEF" />);

    const expectedUrl = new URL("/join/ABCDEF", window.location.origin).href;
    expect(screen.getByDisplayValue(expectedUrl)).toBeVisible();
    expect(screen.getByRole("link", { name: "打开加入页" })).toHaveAttribute(
      "href",
      expectedUrl,
    );
    expect(await screen.findByAltText("玩家加入二维码")).toHaveAttribute(
      "src",
      "data:image/png;base64,qr",
    );
  });

  it("copies the invite URL", async () => {
    render(<RoomInvitePanel roomCode="ABCDEF" />);

    const expectedUrl = new URL("/join/ABCDEF", window.location.origin).href;
    fireEvent.click(screen.getByRole("button", { name: "复制邀请链接" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expectedUrl));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "已复制链接" })).toBeVisible(),
    );
    expect(screen.getByDisplayValue(expectedUrl)).toBeVisible();
  });

  it("falls back when the clipboard API rejects", async () => {
    writeText.mockRejectedValue(new Error("denied"));
    render(<RoomInvitePanel roomCode="ABCDEF" />);

    fireEvent.click(screen.getByRole("button", { name: "复制邀请链接" }));

    await waitFor(() => expect(execCommand).toHaveBeenCalledWith("copy"));
    expect(screen.getByRole("button", { name: "已复制链接" })).toBeVisible();
  });
});
