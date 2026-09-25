import {
  parseServerMessage,
  type ServerMessage,
} from "../protocol/parseServerMessage";

import { reconnectDecision } from "./reconnect";

export type WebSocketCtor = new (url: string) => WebSocket;

export interface RoomSocketConfig {
  url: string;
  WebSocketCtor?: WebSocketCtor;
  authTimeoutMs?: number;
}

export type RoomSocketState =
  | "idle"
  | "connecting"
  | "awaiting_auth"
  | "ready"
  | "retry_wait"
  | "closed";

export type RoomSocketEvent =
  | {
      kind: "state";
      state: RoomSocketState;
      closeCode: number | null;
    }
  | { kind: "message"; message: ServerMessage };

export type RoomSocketListener = (event: RoomSocketEvent) => void;

const DEFAULT_AUTH_TIMEOUT_MS = 5000;

export class RoomSocket {
  private readonly url: string;
  private readonly WebSocketCtor: WebSocketCtor;
  private readonly authTimeoutMs: number;
  private readonly listeners = new Set<RoomSocketListener>();
  private socket: WebSocket | null = null;
  private token: string | null = null;
  private state: RoomSocketState = "idle";
  private reconnectAttempt = 0;
  private generation = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private authTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = false;

  constructor(config: RoomSocketConfig) {
    const WebSocketCtor = config.WebSocketCtor ?? globalThis.WebSocket;
    if (WebSocketCtor === undefined) {
      throw new Error("WebSocket is not available");
    }

    this.url = config.url;
    this.WebSocketCtor = WebSocketCtor;
    this.authTimeoutMs = config.authTimeoutMs ?? DEFAULT_AUTH_TIMEOUT_MS;
  }

  connect(token: string): void {
    if (token.length === 0) {
      throw new Error("RoomSocket token is required");
    }

    this.token = token;
    this.shouldReconnect = true;
    this.reconnectAttempt = 0;
    this.clearReconnectTimer();
    this.closeCurrent(1000, "reconnect");
    this.open();
  }

  send(payload: unknown): boolean {
    const socket = this.socket;
    if (socket === null || socket.readyState !== 1) {
      return false;
    }

    const frame = JSON.stringify(payload);
    if (frame === undefined) {
      return false;
    }
    socket.send(frame);
    return true;
  }

  disconnect(code = 1000, reason = "client disconnect"): void {
    this.shouldReconnect = false;
    this.clearReconnectTimer();
    this.clearAuthTimer();
    this.closeCurrent(code, reason);
    this.setState("closed", code);
  }

  subscribe(listener: RoomSocketListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private open(): void {
    if (!this.shouldReconnect || this.token === null) {
      return;
    }

    this.setState("connecting", null);
    const generation = ++this.generation;
    const socket = new this.WebSocketCtor(this.url);
    this.socket = socket;

    socket.onopen = () => {
      if (!this.isCurrent(socket, generation) || !this.shouldReconnect) {
        return;
      }
      this.setState("awaiting_auth", null);
      this.send({
        type: "auth",
        token: this.token,
        last_seq: 0,
      });
      this.authTimer = setTimeout(() => {
        if (
          this.isCurrent(socket, generation) &&
          this.state === "awaiting_auth"
        ) {
          socket.close(4002, "auth timeout");
        }
      }, this.authTimeoutMs);
    };

    socket.onmessage = (event) => {
      if (!this.isCurrent(socket, generation) || typeof event.data !== "string") {
        return;
      }

      let payload: unknown;
      try {
        payload = JSON.parse(event.data) as unknown;
      } catch {
        return;
      }

      const parsed = parseServerMessage(payload);
      if (parsed.kind === "ignored") {
        return;
      }
      if (parsed.message.type === "session.ready") {
        this.clearAuthTimer();
        this.reconnectAttempt = 0;
        this.setState("ready", null);
      }
      this.emit({ kind: "message", message: parsed.message });
    };

    socket.onclose = (event) => {
      if (!this.isCurrent(socket, generation)) {
        return;
      }
      this.socket = null;
      this.clearAuthTimer();
      this.handleClose(event.code);
    };
  }

  private handleClose(closeCode: number): void {
    const decision = reconnectDecision(closeCode, this.reconnectAttempt);
    if (decision.kind === "stop") {
      this.shouldReconnect = false;
      this.setState("closed", closeCode);
      return;
    }

    this.reconnectAttempt += 1;
    this.setState("retry_wait", closeCode);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, decision.delayMs);
  }

  private isCurrent(socket: WebSocket, generation: number): boolean {
    return this.socket === socket && this.generation === generation;
  }

  private closeCurrent(code: number, reason: string): void {
    const socket = this.socket;
    this.socket = null;
    this.generation += 1;
    if (socket === null) {
      return;
    }

    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.close(code, reason);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private clearAuthTimer(): void {
    if (this.authTimer !== null) {
      clearTimeout(this.authTimer);
      this.authTimer = null;
    }
  }

  private setState(state: RoomSocketState, closeCode: number | null): void {
    this.state = state;
    this.emit({ kind: "state", state, closeCode });
  }

  private emit(event: RoomSocketEvent): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }
}
