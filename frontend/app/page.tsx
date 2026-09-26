"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import type { RemoteParticipant, TranscriptionSegment } from "livekit-client";

import { ChatPanel } from "@/components/ChatPanel";
import { ChatIcon, ChevronIcon, MicIcon, PhoneDownIcon } from "@/components/icons";
import { VoiceOrb } from "@/components/VoiceOrb";
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
const CHAT_PANEL_ID = "transcript-panel";
const WIDE_LAYOUT_QUERY = "(min-width: 960px)";

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function upsertTranscript(
  prev: TranscriptEntry[],
  seg: TranscriptionSegment,
  speaker: "you" | "aisha",
): TranscriptEntry[] {
  const idx = prev.findIndex((e) => e.id === seg.id);
  const time = idx === -1 ? Date.now() : prev[idx].time;
  const entry: TranscriptEntry = { id: seg.id, speaker, text: seg.text, final: seg.final, time };
  if (idx === -1) return [...prev, entry];
  const next = [...prev];
  next[idx] = entry;
  return next;
}

function formatElapsed(totalSeconds: number) {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const AGENT_STATE_LABEL: Record<AgentState, string> = {
  idle: "Ready when you are",
  initializing: "Connecting to Aisha…",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

const MIC_LABEL: Record<MicState, string> = {
  not_started: "Mic off",
  requesting: "Requesting mic…",
  granted: "Mic on",
  denied: "Mic blocked",
};

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  idle: "Not connected",
  connecting: "Connecting",
  connected: "Connected",
  ending: "Ending",
  disconnected: "Call ended",
  error: "Connection error",
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
  const [chatOpen, setChatOpen] = useState(false);
  const [seenCount, setSeenCount] = useState(0);
  const [elapsed, setElapsed] = useState(0);

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

  // Messages count as read while the panel is open; the badge shows the rest.
  useEffect(() => {
    if (chatOpen) setSeenCount(transcript.length);
  }, [chatOpen, transcript.length]);

  const unread = Math.max(0, transcript.length - seenCount);

  // Close the mobile sheet with Escape.
  useEffect(() => {
    if (!chatOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setChatOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chatOpen]);

  const isConnecting = connectionState === "connecting";
  const isEnding = connectionState === "ending";
  const isBusy = isConnecting || isEnding;
  const isConnected = connectionState === "connected";

  useEffect(() => {
    if (!isConnected) return;
    const startedAt = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [isConnected]);

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
    setSeenCount(0);
    setExtraction(null);
    setCallId(null);
    setConnectionState("connecting");
    setMicState("requesting");
    if (window.matchMedia(WIDE_LAYOUT_QUERY).matches) setChatOpen(true);
    appendLog("Requesting LiveKit access token from backend...");

    try {
      const tokenRes = await createToken({ business_id: BUSINESS_ID });
      roomNameRef.current = tokenRes.room_name;
      appendLog(`Token received. room=${tokenRes.room_name} identity=${tokenRes.identity}`);

      const room = new Room({ adaptiveStream: true, dynacast: true });
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

      room.on(RoomEvent.ParticipantDisconnected, (participant: RemoteParticipant) => {
        // The agent leaves on its own only when its session failed (e.g.
        // repeated STT/LLM/TTS provider errors). Tell the caller instead of
        // leaving them talking into a silent room.
        if (!participant.isAgent) return;
        appendLog(`Aisha left the room (identity=${participant.identity})`);
        setAgentState("idle");
        setErrorMessage(
          "Aisha got disconnected because of a technical problem. Please end the conversation and try again.",
        );
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
    setMicState("not_started");

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

  const languages = useMemo(
    () =>
      (business?.supported_languages ?? ["English", "Hindi", "Hinglish"])
        .map((l) => {
          const lower = l.toLowerCase();
          if (lower.includes("hinglish")) return "Hinglish";
          if (lower.startsWith("en")) return "English";
          if (lower.startsWith("hi")) return "Hindi";
          return l;
        })
        .filter((v, i, arr) => arr.indexOf(v) === i),
    [business],
  );

  const micTone =
    micState === "granted" ? "on" : micState === "requesting" ? "pending" : micState === "denied" ? "error" : "off";
  const connectionTone =
    connectionState === "connected"
      ? "on"
      : isBusy
        ? "pending"
        : connectionState === "error"
          ? "error"
          : "off";

  const agentName = business?.agent_name ?? "Aisha";

  return (
    <div className={`shell${chatOpen ? " chat-open" : ""}`}>
      <audio ref={audioElRef} autoPlay />
      <div className="backdrop-blob b1" aria-hidden />
      <div className="backdrop-blob b2" aria-hidden />
      <div className="backdrop-blob b3" aria-hidden />

      <main className="main">
        <header className="hero-header">
          <span className="eyebrow">{business?.name ?? "Sharma Dental Care"}</span>
          <h1 className="headline">
            Meet {agentName}.
            <span className="headline-soft"> Your clinic&rsquo;s voice.</span>
          </h1>
          <p className="subhead">
            An AI receptionist who books appointments, answers questions and speaks the way your
            patients do.
          </p>
          <ul className="lang-chips" aria-label="Supported languages">
            {languages.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </header>

        {errorMessage && (
          <div className="error-banner" role="alert">
            {errorMessage}
          </div>
        )}

        <section className="call-card" aria-label="Voice call">
          <VoiceOrb state={agentState} initial={agentName.charAt(0)} />

          <div className="call-state" aria-live="polite">
            <span className={`state-label ${agentState}`}>{AGENT_STATE_LABEL[agentState]}</span>
            <span className="call-timer">{isConnected ? formatElapsed(elapsed) : " "}</span>
          </div>

          <div className="call-actions">
            {!isConnected ? (
              <button className="btn btn-primary" onClick={handleStart} disabled={isBusy}>
                <MicIcon />
                {isConnecting ? "Connecting…" : "Start conversation"}
              </button>
            ) : (
              <button className="btn btn-end" onClick={handleEnd} disabled={isEnding}>
                <PhoneDownIcon />
                {isEnding ? "Ending…" : "End conversation"}
              </button>
            )}
            <button
              className={`btn btn-secondary${chatOpen ? " active" : ""}`}
              onClick={() => setChatOpen((o) => !o)}
              aria-expanded={chatOpen}
              aria-controls={CHAT_PANEL_ID}
            >
              <ChatIcon />
              {chatOpen ? "Hide transcript" : "Transcript"}
              {!chatOpen && unread > 0 && <span className="badge">{unread}</span>}
            </button>
          </div>

          <div className="status-chips">
            <span className={`chip ${micTone}`}>
              <span className="dot" /> {MIC_LABEL[micState]}
            </span>
            <span className={`chip ${connectionTone}`}>
              <span className="dot" /> {CONNECTION_LABEL[connectionState]}
            </span>
            {callId && <span className="chip mono">#{callId.slice(0, 8)}</span>}
          </div>
        </section>

        {(extraction || waitingForResult) && (
          <section className="result-card" aria-label="Call result">
            <div className="result-head">
              <h2 className="card-title">Call summary</h2>
              {extraction && (
                <span
                  className={`outcome-badge${extraction.outcome === "appointment_booked" ? " booked" : ""}`}
                >
                  {(extraction.outcome ?? "unknown").replaceAll("_", " ")}
                </span>
              )}
            </div>
            {waitingForResult && !extraction && (
              <p className="muted shimmer-text">Putting together the call summary…</p>
            )}
            {extraction && (
              <>
                {extraction.summary && <p className="result-summary">{extraction.summary}</p>}
                <dl className="result-grid">
                  {(
                    [
                      ["Customer", extraction.customer_name],
                      ["Phone", extraction.customer_phone],
                      ["Service", extraction.service],
                      ["Language", extraction.language],
                      ["Date", extraction.appointment_date],
                      ["Time", extraction.appointment_time],
                    ] as const
                  ).map(([label, value]) => (
                    <div key={label} className="result-field">
                      <dt>{label}</dt>
                      <dd>{value ?? "—"}</dd>
                    </div>
                  ))}
                </dl>
              </>
            )}
          </section>
        )}

        <details className="debug">
          <summary>
            <ChevronIcon className="debug-chevron" width={14} height={14} />
            Debug log <span className="muted">({debugLog.length})</span>
          </summary>
          <div className="debug-log" ref={debugLogRef}>
            {debugLog.length === 0 ? "No events yet." : debugLog.join("\n")}
          </div>
        </details>

        <p className="footer-note">
          V0 demo · fake business data · not for real patient use
        </p>
      </main>

      <div className="chat-scrim" onClick={() => setChatOpen(false)} aria-hidden />
      <ChatPanel
        id={CHAT_PANEL_ID}
        open={chatOpen}
        agentName={agentName}
        agentState={agentState}
        entries={transcript}
        onClose={() => setChatOpen(false)}
      />
    </div>
  );
}
