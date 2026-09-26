"use client";

import { useEffect, useRef } from "react";

import type { AgentState, TranscriptEntry } from "@/lib/types";
import { CloseIcon, TicksIcon } from "./icons";

const PRESENCE: Record<AgentState, string> = {
  idle: "offline",
  initializing: "connecting…",
  listening: "online",
  thinking: "typing…",
  speaking: "speaking…",
};

function formatTime(ms: number) {
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

interface ChatPanelProps {
  id: string;
  open: boolean;
  agentName: string;
  agentState: AgentState;
  entries: TranscriptEntry[];
  onClose: () => void;
}

export function ChatPanel({ id, open, agentName, agentState, entries, onClose }: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const showTyping = agentState === "thinking";

  // Keep the newest message in view, like a chat app does.
  useEffect(() => {
    if (!open) return;
    const el = scrollRef.current;
    el?.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [entries, showTyping, open]);

  return (
    <aside id={id} className={`chat${open ? " open" : ""}`} aria-label="Conversation transcript" aria-hidden={!open}>
      <header className="chat-header">
        <div className="chat-avatar" aria-hidden>
          {agentName.charAt(0)}
        </div>
        <div className="chat-title">
          <span className="chat-name">{agentName}</span>
          <span className={`chat-presence ${agentState}`}>{PRESENCE[agentState]}</span>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Close transcript" tabIndex={open ? 0 : -1}>
          <CloseIcon />
        </button>
      </header>

      <div className="chat-body" ref={scrollRef}>
        <div className="chat-day">Today</div>
        <div className="chat-notice">
          Messages are transcribed live from your voice call with {agentName}.
        </div>

        {entries.map((entry, i) => {
          const firstOfGroup = i === 0 || entries[i - 1].speaker !== entry.speaker;
          return (
            <div
              key={entry.id}
              className={`msg-row ${entry.speaker}${firstOfGroup ? " first" : ""}`}
            >
              <div className={`msg${entry.final ? "" : " interim"}`}>
                <span className="msg-text">{entry.text}</span>
                <span className="msg-meta">
                  {formatTime(entry.time)}
                  {entry.speaker === "you" && <TicksIcon read={entry.final} />}
                </span>
              </div>
            </div>
          );
        })}

        {showTyping && (
          <div className={`msg-row aisha${entries.at(-1)?.speaker !== "aisha" ? " first" : ""}`}>
            <div className="msg typing" aria-label={`${agentName} is typing`}>
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
      </div>

      <footer className="chat-footer">
        <span className="chat-footer-pill">Voice call · transcript updates as you speak</span>
      </footer>
    </aside>
  );
}
