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
});
