"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import type { RemoteParticipant, TranscriptionSegment } from "livekit-client";

import { createToken, getBusiness, getCallExtraction, listCallsByRoom } from "@/lib/api";
import type {
  AgentState,
  Business,
  CallExtraction,
  ConnectionState,
  MicState,
  TranscriptEntry,
} from "@/lib/types";

const BUSINESS_ID = process.env.NEXT_PUBLIC_DEFAULT_BUSINESS_ID ?? "sharma-dental";

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function upsertTranscript(
  prev: TranscriptEntry[],
  seg: TranscriptionSegment,
  speaker: "you" | "aisha",
): TranscriptEntry[] {
  const idx = prev.findIndex((e) => e.id === seg.id);
  const entry: TranscriptEntry = { id: seg.id, speaker, text: seg.text, final: seg.final };
  if (idx === -1) return [...prev, entry];
  const next = [...prev];
  next[idx] = entry;
  return next;
}

const AGENT_STATE_LABEL: Record<AgentState, string> = {
  idle: "Idle",
  initializing: "Connecting to Aisha…",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

const MIC_LABEL: Record<MicState, string> = {
  not_started: "Not started",
  requesting: "Requesting permission…",
  granted: "Connected",
  denied: "Permission denied",
};

export default function Home() {
  const [business, setBusiness] = useState<Business | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [agentState, setAgentState] = useState<AgentState>("idle");
  const [micState, setMicState] = useState<MicState>("not_started");
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [debugLog, setDebugLog] = useState<string[]>([]);
  const [callId, setCallId] = useState<string | null>(null);
  const [extraction, setExtraction] = useState<CallExtraction | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [waitingForResult, setWaitingForResult] = useState(false);

  const roomRef = useRef<Room | null>(null);
  const roomNameRef = useRef<string | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const debugLogRef = useRef<HTMLDivElement | null>(null);

  const appendLog = useCallback((line: string) => {
    const stamp = new Date().toLocaleTimeString();
    setDebugLog((prev) => [...prev.slice(-199), `[${stamp}] ${line}`]);
  }, []);

  useEffect(() => {
    getBusiness(BUSINESS_ID).catch((err) => {
      setErrorMessage(
        `Could not load business info from the backend: ${
          err instanceof Error ? err.message : String(err)
        }. Is the backend running?`,
      );
    }).then((b) => b && setBusiness(b));
  }, []);

  useEffect(() => {
    debugLogRef.current?.scrollTo({ top: debugLogRef.current.scrollHeight });
  }, [debugLog]);

  useEffect(() => {
    return () => {
      roomRef.current?.disconnect();
    };
  }, []);

  const pollForCallId = useCallback(
    async (roomName: string) => {
      for (let i = 0; i < 10; i++) {
        try {
          const calls = await listCallsByRoom(roomName);
          if (calls.length > 0) {
            setCallId(calls[0].id);
            appendLog(`Call ID: ${calls[0].id}`);
            return;
          }
        } catch {
          // transient — agent may not have created the Call row yet
        }
        await sleep(1500);
      }
    },
    [appendLog],
  );

  const handleStart = useCallback(async () => {
    setErrorMessage(null);
    setTranscript([]);
    setExtraction(null);
    setCallId(null);
    setConnectionState("connecting");
    setMicState("requesting");
    appendLog("Requesting LiveKit access token from backend...");

    try {
      const tokenRes = await createToken({ business_id: BUSINESS_ID });
      roomNameRef.current = tokenRes.room_name;
      appendLog(`Token received. room=${tokenRes.room_name} identity=${tokenRes.identity}`);

      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
        // Explicit instead of relying on browser/WebRTC defaults: boosts quiet
        // speech (autoGainControl) and suppresses room noise/echo of Aisha's
        // own TTS bleeding back into the mic, both of which can otherwise
        // trigger Sarvam STT's server-side VAD on non-speech audio.
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      roomRef.current = room;

      room.on(RoomEvent.Connected, () => appendLog("Connected to LiveKit room"));

      room.on(RoomEvent.ParticipantConnected, (participant: RemoteParticipant) => {
        appendLog(`Aisha joined (identity=${participant.identity})`);
        const state = participant.attributes["lk.agent.state"];
        if (state) setAgentState(state as AgentState);
        else setAgentState("listening");
      });

      room.on(RoomEvent.ParticipantAttributesChanged, (changed, participant) => {
        if (participant.isLocal) return;
        const state = changed["lk.agent.state"];
        if (state) {
          setAgentState(state as AgentState);
          appendLog(`Agent state -> ${state}`);
        }
      });

      room.on(RoomEvent.TrackSubscribed, (track, _pub, participant) => {
        if (track.kind === Track.Kind.Audio && !participant.isLocal) {
          if (audioElRef.current) track.attach(audioElRef.current);
          appendLog("Subscribed to Aisha's audio track");
        }
      });

      room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        const isLocal = participant?.identity === room.localParticipant.identity;
        const speaker: "you" | "aisha" = isLocal ? "you" : "aisha";
        setTranscript((prev) => segments.reduce((acc, seg) => upsertTranscript(acc, seg, speaker), prev));
        const finalSeg = segments.find((s) => s.final);
        if (finalSeg) appendLog(`${speaker === "you" ? "YOU" : "AISHA"}: ${finalSeg.text}`);
      });

      room.on(RoomEvent.Disconnected, (reason) => {
        appendLog(`Room disconnected${reason ? ` (reason=${reason})` : ""}`);
        setAgentState("idle");
      });

      await room.connect(tokenRes.url, tokenRes.token);
      setConnectionState("connected");
      setAgentState("initializing");

      try {
        await room.localParticipant.setMicrophoneEnabled(true);
        setMicState("granted");
        appendLog("Microphone enabled and publishing");
      } catch (micErr) {
        setMicState("denied");
        appendLog(
          `Microphone error: ${micErr instanceof Error ? micErr.message : String(micErr)}`,
        );
      }

      void pollForCallId(tokenRes.room_name);
    } catch (err) {
      setConnectionState("error");
      const message = err instanceof Error ? err.message : "Failed to start conversation";
      setErrorMessage(message);
      setMicState("not_started");
      appendLog(`Error starting conversation: ${message}`);
    }
  }, [appendLog, pollForCallId]);

  const handleEnd = useCallback(async () => {
    setConnectionState("ending");
    appendLog("Ending conversation...");
    roomRef.current?.disconnect();
    roomRef.current = null;

    const roomName = roomNameRef.current;
    setConnectionState("disconnected");
    setAgentState("idle");

    if (roomName) {
      setWaitingForResult(true);
      await sleep(1200);
      for (let i = 0; i < 14; i++) {
        try {
          const calls = await listCallsByRoom(roomName);
          if (calls.length > 0) {
            const call = calls[0];
            setCallId(call.id);
            if (call.status !== "in_progress") {
              try {
                const result = await getCallExtraction(call.id);
                setExtraction(result);
                appendLog("Post-call extraction ready");
                break;
              } catch {
                appendLog("Post-call extraction still processing...");
              }
            }
          }
        } catch {
          // ignore transient network errors while polling
        }
        await sleep(1500);
      }
      setWaitingForResult(false);
    }
  }, [appendLog]);

  const micDotClass = useMemo(() => {
    if (micState === "granted") return "dot on";
    if (micState === "requesting") return "dot pending";
    if (micState === "denied") return "dot error";
    return "dot off";
  }, [micState]);

  const connectionDotClass = useMemo(() => {
    if (connectionState === "connected") return "dot on";
    if (connectionState === "connecting" || connectionState === "ending") return "dot pending";
    if (connectionState === "error") return "dot error";
    return "dot off";
  }, [connectionState]);

  const isConnecting = connectionState === "connecting";
  const isEnding = connectionState === "ending";
  const isBusy = isConnecting || isEnding;
  const isConnected = connectionState === "connected";

  return (
    <main className="page">
      <audio ref={audioElRef} autoPlay />

      <header className="header">
        <span className="business-name">{business?.name ?? "Sharma Dental Care"}</span>
        <span className="agent-name">{business?.agent_name ?? "Aisha"}</span>
        <span className="agent-role">AI Receptionist</span>
        <span className="languages">
          {(business?.supported_languages ?? ["English", "Hindi", "Hinglish"])
            .map((l) => {
              const lower = l.toLowerCase();
              if (lower.includes("hinglish")) return "Hinglish";
              if (lower.startsWith("en")) return "English";
              if (lower.startsWith("hi")) return "Hindi";
              return l;
            })
            .filter((v, i, arr) => arr.indexOf(v) === i)
            .join(" • ")}
        </span>
      </header>

      {errorMessage && <div className="error-banner">{errorMessage}</div>}

      <section className="card">
        <div className="control-row">
          {!isConnected ? (
            <button className="primary" onClick={handleStart} disabled={isBusy}>
              {isConnecting ? "Connecting…" : "Start Conversation"}
            </button>
          ) : (
            <button className="primary stop" onClick={handleEnd} disabled={isEnding}>
              {isEnding ? "Ending…" : "End Conversation"}
            </button>
          )}
          <span className={`agent-pill ${agentState}`}>{AGENT_STATE_LABEL[agentState]}</span>
        </div>
        <div className="status-line" style={{ marginTop: 16 }}>
          <span className="status-item">
            <span className={micDotClass} /> Microphone: {MIC_LABEL[micState]}
          </span>
          <span className="status-item">
            <span className={connectionDotClass} /> Connection: {connectionState}
          </span>
          {callId && <span className="status-item">Call ID: {callId.slice(0, 8)}…</span>}
        </div>
      </section>

      <section className="card">
        <p className="section-title">Live Transcript</p>
        <div className="transcript">
          {transcript.length === 0 && (
            <p className="transcript-empty">
              Transcript will appear here once the conversation starts.
            </p>
          )}
          {transcript.map((entry) => (
            <div key={entry.id} className={`bubble-row ${entry.speaker}`}>
              <div className={`bubble${entry.final ? "" : " interim"}`}>
                <span className="speaker-label">{entry.speaker === "you" ? "You" : "Aisha"}</span>
                {entry.text}
              </div>
            </div>
          ))}
        </div>
      </section>

      {(extraction || waitingForResult) && (
        <section className="card">
          <p className="section-title">Call Result</p>
          {waitingForResult && !extraction && (
            <p className="transcript-empty">Processing call summary…</p>
          )}
          {extraction && (
            <>
              <span
                className={`outcome-badge${extraction.outcome === "appointment_booked" ? " booked" : ""}`}
              >
                {(extraction.outcome ?? "unknown").replaceAll("_", " ")}
              </span>
              <div className="result-grid" style={{ marginTop: 16 }}>
                <div className="result-field">
                  <div className="label">Customer</div>
                  <div className="value">{extraction.customer_name ?? "—"}</div>
                </div>
                <div className="result-field">
                  <div className="label">Phone</div>
                  <div className="value">{extraction.customer_phone ?? "—"}</div>
                </div>
                <div className="result-field">
                  <div className="label">Service</div>
                  <div className="value">{extraction.service ?? "—"}</div>
                </div>
                <div className="result-field">
                  <div className="label">Language</div>
                  <div className="value">{extraction.language ?? "—"}</div>
                </div>
                <div className="result-field">
                  <div className="label">Date</div>
                  <div className="value">{extraction.appointment_date ?? "—"}</div>
                </div>
                <div className="result-field">
                  <div className="label">Time</div>
                  <div className="value">{extraction.appointment_time ?? "—"}</div>
                </div>
              </div>
              {extraction.summary && (
                <div className="result-field" style={{ marginTop: 16 }}>
                  <div className="label">Summary</div>
                  <div className="value" style={{ fontWeight: 400 }}>
                    {extraction.summary}
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      )}

      <details className="debug card">
        <summary>Debug log ({debugLog.length})</summary>
        <div className="debug-log" ref={debugLogRef}>
          {debugLog.length === 0 ? "No events yet." : debugLog.join("\n")}
        </div>
      </details>

      <p className="footer-note">
        V0 demo — fake business data. Not for real patient use. See README for architecture
        and next steps.
      </p>
    </main>
  );
}
