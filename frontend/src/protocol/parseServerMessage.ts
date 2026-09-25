import type {
  CommandType,
  HostControlView,
  LegalAction,
  PrivateFact,
  PublicView,
  RoomSnapshot,
  SeatView,
  VoteSummary,
} from "./models";

export interface AuthRequiredMessage {
  type: "auth.required";
}

export interface SessionReadyMessage {
  type: "session.ready";
  server_time: string;
  snapshot: RoomSnapshot;
}

export interface PublicViewMessage {
  type: "public.view.updated";
  server_time: string;
  outbox_seq: number;
  public_view: PublicView;
}

export interface SeatViewMessage {
  type: "seat.view.updated";
  server_time: string;
  outbox_seq: number;
  seat_id: number;
  seat_view: SeatView;
}

export interface HostControlMessage {
  type: "host.control.updated";
  server_time: string;
  outbox_seq: number;
  host_control: HostControlView;
}

export interface CommandAckMessage {
  type: "command.ack";
  command_id: string;
  accepted: boolean;
  revision: number;
  error_code: string | null;
  outbox_seq: number;
}

export interface PongMessage {
  type: "pong";
}

export interface ErrorMessage {
  type: "error";
  code: string;
  message: string;
  request_id: string;
}

export type ServerMessage =
  | AuthRequiredMessage
  | SessionReadyMessage
  | PublicViewMessage
  | SeatViewMessage
  | HostControlMessage
  | CommandAckMessage
  | PongMessage
  | ErrorMessage;

export type ParsedServerMessage =
  | { kind: "message"; message: ServerMessage }
  | { kind: "ignored" };

type JsonObject = Record<string, unknown>;

const COMMAND_TYPES = new Set<CommandType>([
  "JOIN_ROOM",
  "SET_READY",
  "CONFIRM_ROLE",
  "WOLF_NOMINATE_KILL",
  "SEER_INSPECT",
  "WITCH_USE_ANTIDOTE",
  "WITCH_USE_POISON",
  "WITCH_SKIP",
  "SPEAK",
  "PASS_SPEECH",
  "VOTE",
  "ABSTAIN",
  "HOST_PAUSE",
  "HOST_RESUME",
]);

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

function isIntegerArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every(isInteger);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || isString(value);
}

function isPublicTimelineItem(value: unknown): boolean {
  return (
    isObject(value) &&
    isString(value.event_id) &&
    isInteger(value.revision) &&
    isString(value.event_type) &&
    isString(value.statement)
  );
}

function isVoteSummary(value: unknown): value is VoteSummary {
  return (
    isObject(value) &&
    isString(value.round_id) &&
    Array.isArray(value.tallies) &&
    value.tallies.every(
      (tally) =>
        isObject(tally) &&
        isInteger(tally.seat_id) &&
        isInteger(tally.votes),
    ) &&
    isInteger(value.abstention_count) &&
    isBoolean(value.closed) &&
    isInteger(value.submitted_count) &&
    isInteger(value.eligible_count)
  );
}

function isPublicView(value: unknown): value is PublicView {
  return (
    isObject(value) &&
    (value.schema_version === "public-view.v1" ||
      value.schema_version === "seat-view.v1") &&
    isString(value.room_id) &&
    isInteger(value.revision) &&
    isString(value.phase) &&
    isInteger(value.day) &&
    isIntegerArray(value.living_seats) &&
    Array.isArray(value.public_timeline) &&
    value.public_timeline.every(isPublicTimelineItem) &&
    (value.vote_summary === null || isVoteSummary(value.vote_summary)) &&
    isNullableString(value.deadline_at) &&
    isBoolean(value.paused)
  );
}

function isPrivateFact(value: unknown): value is PrivateFact {
  return (
    isObject(value) &&
    isString(value.fact_id) &&
    isString(value.event_id) &&
    isInteger(value.recipient_seat_id) &&
    isInteger(value.revision) &&
    isString(value.fact_type) &&
    isObject(value.payload)
  );
}

function isCommandType(value: unknown): value is CommandType {
  return typeof value === "string" && COMMAND_TYPES.has(value as CommandType);
}

function isLegalAction(value: unknown): value is LegalAction {
  return (
    isObject(value) &&
    isCommandType(value.action) &&
    isIntegerArray(value.target_seat_ids) &&
    isNullableString(value.deadline_at)
  );
}

function isSeatView(value: unknown): value is SeatView {
  if (!isPublicView(value) || value.schema_version !== "seat-view.v1") {
    return false;
  }
  const record = value as PublicView & JsonObject;
  return (
    isInteger(record.seat_id) &&
    isNullableString(record.role) &&
    Array.isArray(record.private_facts) &&
    record.private_facts.every(isPrivateFact) &&
    Array.isArray(record.legal_actions) &&
    record.legal_actions.every(isLegalAction)
  );
}

function isHostControlView(value: unknown): value is HostControlView {
  return (
    isObject(value) &&
    isPublicView(value.public_view) &&
    value.public_view.schema_version === "public-view.v1" &&
    isBoolean(value.paused) &&
    isInteger(value.revision)
  );
}

function isRoomSnapshot(value: unknown): value is RoomSnapshot {
  return (
    isObject(value) &&
    isString(value.room_id) &&
    isString(value.room_code) &&
    isInteger(value.revision) &&
    isInteger(value.outbox_seq) &&
    isPublicView(value.public_view) &&
    value.public_view.schema_version === "public-view.v1" &&
    (value.seat_view === null || isSeatView(value.seat_view)) &&
    (value.host_control === null ||
      isHostControlView(value.host_control))
  );
}

function accepted(message: ServerMessage): ParsedServerMessage {
  return { kind: "message", message };
}

export function parseServerMessage(value: unknown): ParsedServerMessage {
  if (!isObject(value)) {
    return { kind: "ignored" };
  }

  switch (value.type) {
    case "auth.required":
      return accepted({ type: "auth.required" });
    case "session.ready":
      if (isString(value.server_time) && isRoomSnapshot(value.snapshot)) {
        return accepted({
          type: "session.ready",
          server_time: value.server_time,
          snapshot: value.snapshot,
        });
      }
      break;
    case "public.view.updated":
      if (
        isString(value.server_time) &&
        isInteger(value.outbox_seq) &&
        isPublicView(value.public_view) &&
        value.public_view.schema_version === "public-view.v1"
      ) {
        return accepted({
          type: "public.view.updated",
          server_time: value.server_time,
          outbox_seq: value.outbox_seq,
          public_view: value.public_view,
        });
      }
      break;
    case "seat.view.updated":
      if (
        isString(value.server_time) &&
        isInteger(value.outbox_seq) &&
        isInteger(value.seat_id) &&
        isSeatView(value.seat_view)
      ) {
        return accepted({
          type: "seat.view.updated",
          server_time: value.server_time,
          outbox_seq: value.outbox_seq,
          seat_id: value.seat_id,
          seat_view: value.seat_view,
        });
      }
      break;
    case "host.control.updated":
      if (
        isString(value.server_time) &&
        isInteger(value.outbox_seq) &&
        isHostControlView(value.host_control)
      ) {
        return accepted({
          type: "host.control.updated",
          server_time: value.server_time,
          outbox_seq: value.outbox_seq,
          host_control: value.host_control,
        });
      }
      break;
    case "command.ack":
      if (
        isString(value.command_id) &&
        isBoolean(value.accepted) &&
        isInteger(value.revision) &&
        (value.error_code === null || isString(value.error_code)) &&
        isInteger(value.outbox_seq)
      ) {
        return accepted({
          type: "command.ack",
          command_id: value.command_id,
          accepted: value.accepted,
          revision: value.revision,
          error_code: value.error_code,
          outbox_seq: value.outbox_seq,
        });
      }
      break;
    case "pong":
      return accepted({ type: "pong" });
    case "error":
      if (
        isString(value.code) &&
        isString(value.message) &&
        isString(value.request_id)
      ) {
        return accepted({
          type: "error",
          code: value.code,
          message: value.message,
          request_id: value.request_id,
        });
      }
      break;
  }

  return { kind: "ignored" };
}
