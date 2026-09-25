export type CommandType =
  | "JOIN_ROOM"
  | "SET_READY"
  | "CONFIRM_ROLE"
  | "WOLF_NOMINATE_KILL"
  | "SEER_INSPECT"
  | "WITCH_USE_ANTIDOTE"
  | "WITCH_USE_POISON"
  | "WITCH_SKIP"
  | "SPEAK"
  | "PASS_SPEECH"
  | "VOTE"
  | "ABSTAIN"
  | "HOST_PAUSE"
  | "HOST_RESUME";

export type CommandPayload =
  | { command_type: "JOIN_ROOM"; seat_id: number; display_name: string }
  | { command_type: "SET_READY"; ready: boolean }
  | { command_type: "CONFIRM_ROLE" }
  | { command_type: "WOLF_NOMINATE_KILL"; target_seat_id: number }
  | { command_type: "SEER_INSPECT"; target_seat_id: number }
  | { command_type: "WITCH_USE_ANTIDOTE" }
  | { command_type: "WITCH_USE_POISON"; target_seat_id: number }
  | { command_type: "WITCH_SKIP" }
  | { command_type: "SPEAK"; text: string }
  | { command_type: "PASS_SPEECH" }
  | { command_type: "VOTE"; target_seat_id: number }
  | { command_type: "ABSTAIN" }
  | { command_type: "HOST_PAUSE"; reason: string }
  | { command_type: "HOST_RESUME" };

export interface CommandEnvelope {
  schema_version: "command.v1";
  command_id: string;
  room_id: string;
  expected_revision: number;
  issued_at: string;
  payload: CommandPayload;
}

export interface LegalAction {
  action: CommandType;
  target_seat_ids: number[];
  deadline_at: string | null;
}

export interface PublicTimelineItem {
  event_id: string;
  revision: number;
  event_type: string;
  statement: string;
}

export interface PrivateFact {
  fact_id: string;
  event_id: string;
  recipient_seat_id: number;
  revision: number;
  fact_type: string;
  payload: Record<string, unknown>;
}

export interface VoteSummary {
  round_id: string;
  tallies: Array<{ seat_id: number; votes: number }>;
  abstention_count: number;
  closed: boolean;
  submitted_count: number;
  eligible_count: number;
}

export interface PublicView {
  schema_version: "public-view.v1" | "seat-view.v1";
  room_id: string;
  revision: number;
  phase: string;
  day: number;
  living_seats: number[];
  public_timeline: PublicTimelineItem[];
  vote_summary: VoteSummary | null;
  deadline_at: string | null;
  paused: boolean;
}

export interface SeatView extends PublicView {
  schema_version: "seat-view.v1";
  seat_id: number;
  role: string | null;
  private_facts: PrivateFact[];
  legal_actions: LegalAction[];
}

export interface HostControlView {
  public_view: PublicView;
  paused: boolean;
  revision: number;
}

export interface RoomSnapshot {
  room_id: string;
  room_code: string;
  revision: number;
  outbox_seq: number;
  public_view: PublicView;
  seat_view: SeatView | null;
  host_control: HostControlView | null;
}

export interface PlayerReplay {
  room_id: string;
  revision: number;
  public_timeline: PublicTimelineItem[];
  private_facts: PrivateFact[];
  events: unknown[];
}

export interface HostAuditExport {
  room_id: string;
  revision: number;
  state: Record<string, unknown>;
  raw_events: unknown[];
  dm_trace: unknown[];
  snapshots: unknown[];
}

export interface CreateRoomResponse {
  room_id: string;
  room_code: string;
  host_token: string;
  expires_at: string;
}

export interface JoinRoomResponse {
  room_id: string;
  seat_id: number;
  seat_token: string;
  expires_at: string;
}

export interface DisplayPairingResponse {
  pairing_code: string;
  expires_at: string;
}

export interface DisplaySessionResponse {
  room_id: string;
  display_token: string;
  expires_at: string;
}
