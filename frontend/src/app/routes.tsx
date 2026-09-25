import { useRoutes, type RouteObject } from "react-router-dom";

function HomeRoute() {
  return (
    <main className="grid min-h-screen place-items-center bg-surface px-6 py-12 text-text">
      <section className="grid gap-3 text-center">
        <p className="text-sm font-medium text-text-muted">
          AI 主持 · 真人玩家
        </p>
        <h1 className="text-4xl font-semibold">狼人杀 DM</h1>
      </section>
    </main>
  );
}

const routes: RouteObject[] = [{ path: "/", element: <HomeRoute /> }];

export function AppRoutes() {
  return useRoutes(routes);
}
