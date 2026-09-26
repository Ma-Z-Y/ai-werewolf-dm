import {
  useParams,
  useRoutes,
  type RouteObject,
} from "react-router-dom";

import { HostRoute } from "../features/host/HostRoute";
import { PlayerRoute } from "../features/player/PlayerRoute";
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

const routes: RouteObject[] = [
  { path: "/", element: <HomeScreen /> },
  { path: "/host/:roomCode", element: <HostRouteEntry /> },
  { path: "/join/:roomCode?", element: <PlayerRouteEntry /> },
  { path: "/play/:roomCode", element: <PlayerRouteEntry /> },
  { path: "/stage/:roomCode", element: <StageRouteEntry /> },
];

export function AppRoutes() {
  return useRoutes(routes);
}
