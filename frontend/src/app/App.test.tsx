import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "./App";

it("renders the home route", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "狼人杀 DM" })).toBeVisible();
  expect(screen.getByText("无需下载")).toBeVisible();
  expect(screen.getByText("手机即开")).toBeVisible();
  expect(screen.getByText(/6 人准备后自动开局/)).toBeVisible();
});
