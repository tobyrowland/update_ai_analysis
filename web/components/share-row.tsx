"use client";

import { useState } from "react";

interface ShareRowProps {
  // Absolute URL to share. Pre-built by the server so the dated
  // permalink is baked in (not the bare /consensus URL).
  url: string;
  // Tweet/Bluesky body. URL is appended automatically.
  text: string;
}

const SHARE_BUTTON_CLASS =
  "inline-flex items-center gap-2 px-4 py-2 rounded-lg text-text text-sm font-medium tracking-tight transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-text/40";

const SHARE_BUTTON_STYLE = {
  background:
    "linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01))",
  border: "1px solid rgba(255,255,255,0.12)",
  boxShadow: "inset 0 1px 0 rgba(255,255,255,0.06)",
};

export default function ShareRow({ url, text }: ShareRowProps) {
  const [copied, setCopied] = useState(false);

  const tweetHref = `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(url)}`;
  const blueskyHref = `https://bsky.app/intent/compose?text=${encodeURIComponent(`${text} ${url}`)}`;

  async function copy() {
    const ok = await tryCopy(url);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <a
        href={tweetHref}
        target="_blank"
        rel="noopener noreferrer"
        className={SHARE_BUTTON_CLASS}
        style={SHARE_BUTTON_STYLE}
        aria-label="Share on X"
      >
        <XIcon />
        Tweet
      </a>
      <a
        href={blueskyHref}
        target="_blank"
        rel="noopener noreferrer"
        className={SHARE_BUTTON_CLASS}
        style={SHARE_BUTTON_STYLE}
        aria-label="Post to Bluesky"
      >
        <BlueskyIcon />
        Bluesky
      </a>
      <button
        type="button"
        onClick={copy}
        className={SHARE_BUTTON_CLASS}
        style={SHARE_BUTTON_STYLE}
        aria-label={copied ? "Link copied" : "Copy link"}
      >
        {copied ? <CheckIcon /> : <LinkIcon />}
        {copied ? "Copied" : "Copy link"}
      </button>
    </div>
  );
}

// Same fallback shim as web/components/home-prompt.tsx — modern path
// uses navigator.clipboard, falls back to a hidden textarea + execCommand
// for the rare insecure-context case (e.g. previews on http).
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

function XIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden
    >
      <path d="M9.45 7.06 14.5 1.5h-1.43L8.84 6.27 5.46 1.5H1.5l5.3 7.45L1.5 14.5h1.43l4.62-5.13 3.57 5.13h3.96L9.45 7.06zm-1.64 1.83-.54-.74L3.45 2.55h2.2l3.45 4.7.54.75 4.46 6.07h-2.2L7.81 8.89z" />
    </svg>
  );
}

function BlueskyIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden
    >
      <path d="M3.65 2.62C5.45 3.94 7.4 6.61 8 8c.6-1.39 2.55-4.06 4.35-5.38 1.3-.95 3.4-1.69 3.4.66 0 .47-.27 3.93-.43 4.5-.55 1.97-2.6 2.47-4.4 2.17 3.16.53 3.96 2.27 2.22 4-3.3 3.27-4.74-.83-5.11-1.88a4.74 4.74 0 0 1-.03-.1 4.74 4.74 0 0 1-.03.1c-.37 1.05-1.81 5.15-5.11 1.88-1.74-1.73-.94-3.47 2.22-4-1.8.3-3.85-.2-4.4-2.17C.52 7.21.25 3.75.25 3.28c0-2.35 2.1-1.61 3.4-.66z" />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M6.5 9.5a3 3 0 0 0 4.24 0l2.12-2.12a3 3 0 0 0-4.24-4.24l-1.06 1.06" />
      <path d="M9.5 6.5a3 3 0 0 0-4.24 0L3.14 8.62a3 3 0 0 0 4.24 4.24l1.06-1.06" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="m3 8 3.5 3.5L13 5" />
    </svg>
  );
}
