import {
  useParams,
  useRoutes,
  type RouteObject,
} from "react-router-dom";

import { HostRoute } from "../features/host/HostRoute";
import { PlayerRoute } from "../features/player/PlayerRoute";
import { ReplayRoute } from "../features/replay/ReplayRoute";
import { StageRoute } from "../features/stage/StageRoute";

import { HomeScreen } from "./HomeScreen";

function PlayerRouteEntry() {
  const { roomCode = "" } = useParams();
  return <PlayerRoute key={roomCode} />;
}

function HostRouteEntry() {
  const { roomCode = "" } = useParams();
  return <HostRoute key={roomCode} />;
}

function StageRouteEntry() {
  const { roomCode = "" } = useParams();
  return <StageRoute key={roomCode} />;
}

function ReplayRouteEntry() {
  const { roomCode = "" } = useParams();
  return <ReplayRoute key={roomCode} />;
}

const routes: RouteObject[] = [
  { path: "/", element: <HomeScreen /> },
  { path: "/host/:roomCode", element: <HostRouteEntry /> },
  { path: "/join/:roomCode?", element: <PlayerRouteEntry /> },
  { path: "/play/:roomCode", element: <PlayerRouteEntry /> },
  { path: "/replay/:roomCode", element: <ReplayRouteEntry /> },
  { path: "/stage/:roomCode", element: <StageRouteEntry /> },
];

export function AppRoutes() {
  return useRoutes(routes);
}
