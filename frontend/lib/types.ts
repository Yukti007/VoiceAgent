export interface Business {
  id: string;
  name: string;
  agent_name: string;
  description: string;
  default_language: string;
  supported_languages: string[];
  greeting: string;
}

export interface TokenResponse {
  token: string;
  url: string;
  room_name: string;
  identity: string;
}

export interface CallExtraction {
  intent: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  language: string | null;
  service: string | null;
  appointment_date: string | null;
  appointment_time: string | null;
  outcome: string | null;
  requires_followup: boolean;
  summary: string | null;
}

export interface CallSummary {
  id: string;
  business_id: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  status: string;
  detected_language: string | null;
  outcome: string | null;
  summary: string | null;
}

export type ConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "ending"
  | "disconnected"
  | "error";

export type AgentState = "idle" | "initializing" | "listening" | "thinking" | "speaking";

export type MicState = "not_started" | "requesting" | "granted" | "denied";

export interface TranscriptEntry {
  id: string;
  speaker: "you" | "aisha";
  text: string;
  final: boolean;
  /** Epoch ms when the segment first arrived; kept stable across interim updates. */
  time: number;
}
