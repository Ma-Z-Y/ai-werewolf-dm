import {
  useParams,
  useRoutes,
  type RouteObject,
} from "react-router-dom";

import { PlayerRoute } from "../features/player/PlayerRoute";
import { StageRoute } from "../features/stage/StageRoute";

import { HomeScreen } from "./HomeScreen";

function PlayerRouteEntry() {
  const { roomCode = "" } = useParams();
  return <PlayerRoute key={roomCode} />;
}

function HostRoutePlaceholder() {
  const { roomCode = "" } = useParams();
  return (
    <main className="grid min-h-screen place-items-center bg-surface px-6 py-12 text-text">
      <section className="grid gap-3 text-center">
        <p className="text-sm font-medium text-text-muted">主持人房间</p>
        <h1 className="text-3xl font-semibold">房间码 {roomCode}</h1>
      </section>
    </main>
  );
}

function StageRouteEntry() {
  const { roomCode = "" } = useParams();
  return <StageRoute key={roomCode} />;
}

const routes: RouteObject[] = [
  { path: "/", element: <HomeScreen /> },
  { path: "/host/:roomCode", element: <HostRoutePlaceholder /> },
  { path: "/join/:roomCode?", element: <PlayerRouteEntry /> },
  { path: "/play/:roomCode", element: <PlayerRouteEntry /> },
  { path: "/stage/:roomCode", element: <StageRouteEntry /> },
];

export function AppRoutes() {
  return useRoutes(routes);
}
