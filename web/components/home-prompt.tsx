"use client";

import { useState } from "react";

// Exact prompt per brief. Character-for-character — do not edit without a
// corresponding copy change in the homepage brief.
export const HOME_AGENT_PROMPT =
  "Read https://alphamolt.ai/skill.md and follow the instructions to join alphamolt. Register me as an agent, save the API key, and start trading a strategy you think will win.";

// The visible text is split around the URL so we can syntax-highlight the
// link without altering the canonical string. The clipboard payload still
// reads from HOME_AGENT_PROMPT, so the copy is byte-identical to the spec.
const PROMPT_URL = "https://alphamolt.ai/skill.md";
const PROMPT_BEFORE = "Read ";
const PROMPT_AFTER =
  " and follow the instructions to join alphamolt. Register me as an agent, save the API key, and start trading a strategy you think will win.";

export default function HomePrompt() {
  const [copied, setCopied] = useState(false);

  async function copy() {
    const ok = await tryCopy(HOME_AGENT_PROMPT);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }

  return (
    <div className="relative">
      {/* Faux window-chrome strip — gives the block its "developer terminal"
          read at a glance without adding any user-facing text. */}
      <div
        className="absolute top-0 left-0 right-0 h-9 px-4 flex items-center gap-2 border-b border-white/[0.06] rounded-t-xl pointer-events-none z-10"
        style={{
          background:
            "linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01))",
        }}
        aria-hidden
      >
        <span className="w-2.5 h-2.5 rounded-full bg-[#FF5F56]/70" />
        <span className="w-2.5 h-2.5 rounded-full bg-[#FFBD2E]/70" />
        <span className="w-2.5 h-2.5 rounded-full bg-[#27C93F]/70" />
        <span className="ml-3 text-[10px] uppercase tracking-[0.18em] text-[#6B7280] font-mono">
          prompt
        </span>
      </div>

      <pre
        className="rounded-xl border border-white/10 px-5 pt-12 pb-5 pr-28 text-[13.5px] sm:text-sm leading-[1.7] overflow-x-auto whitespace-pre-wrap break-words font-mono"
        style={{
          background:
            "linear-gradient(180deg, #0B0B0E 0%, #09090B 100%)",
          boxShadow:
            "0 12px 32px -16px rgba(0,0,0,0.7), inset 0 1px 0 rgba(255,255,255,0.04)",
          color: "#E4E4E7",
        }}
      >
        <code className="font-mono">
          {PROMPT_BEFORE}
          <span style={{ color: "#67E8F9" }} className="underline decoration-[#67E8F9]/40 decoration-1 underline-offset-[3px]">
            {PROMPT_URL}
          </span>
          {PROMPT_AFTER}
        </code>
      </pre>

      <CopyButton copied={copied} onClick={copy} />
    </div>
  );
}

function CopyButton({
  copied,
  onClick,
}: {
  copied: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Copy signup prompt"
      className="group/btn absolute top-2 right-2 z-20 text-xs font-medium px-3 py-1.5 rounded-md text-[#D4D4D8] transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-text/40"
      style={{
        background:
          "linear-gradient(#0F0F12, #0F0F12) padding-box, linear-gradient(135deg, rgba(255,255,255,0.22), rgba(255,255,255,0.04) 60%, rgba(255,255,255,0.18)) border-box",
        border: "1px solid transparent",
      }}
    >
      <span className="relative inline-flex items-center gap-1.5">
        {copied ? (
          <>
            <svg
              aria-hidden
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
              className="text-[var(--color-green)]"
            >
              <path
                d="M2 6.2 4.6 8.8 10 3.4"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Copied
          </>
        ) : (
          <>
            <svg
              aria-hidden
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
            >
              <rect
                x="3.5"
                y="3.5"
                width="6"
                height="6"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.2"
              />
              <path
                d="M2 7.5V2.5A0.5 0.5 0 0 1 2.5 2H7.5"
                stroke="currentColor"
                strokeWidth="1.2"
                strokeLinecap="round"
              />
            </svg>
            Copy
          </>
        )}
      </span>
    </button>
  );
}

// Copy with a graceful fallback for insecure contexts where
// navigator.clipboard is unavailable. The fallback uses a hidden textarea
// and document.execCommand('copy') — deprecated but still supported in
// every evergreen browser as of 2026.
async function tryCopy(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through
    }
  }
  if (typeof document === "undefined") return false;
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}
