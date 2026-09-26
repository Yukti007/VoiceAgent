import type { AgentState } from "@/lib/types";

/** Pastel orb that breathes, ripples, or shimmers depending on the agent's state. */
export function VoiceOrb({ state, initial }: { state: AgentState; initial: string }) {
  return (
    <div className={`orb ${state}`} aria-hidden>
      <span className="orb-ring r1" />
      <span className="orb-ring r2" />
      <span className="orb-core">
        {state === "speaking" ? (
          <span className="orb-bars">
            <i />
            <i />
            <i />
            <i />
            <i />
          </span>
        ) : (
          <span className="orb-initial">{initial}</span>
        )}
      </span>
    </div>
  );
}
