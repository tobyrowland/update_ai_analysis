"use client";

import Link from "next/link";
import { useSessionEmail } from "@/lib/use-session";

// Auth chip for the nav — "Sign in" when logged out; the email + "Sign out"
// when signed in. Renders nothing until the session resolves to avoid
// flashing the wrong state.
export default function NavAuth({ onNavigate }: { onNavigate?: () => void }) {
  const { email, ready } = useSessionEmail();

  if (!ready) {
    return null;
  }

  if (!email) {
    return (
      <Link
        href="/login"
        onClick={onNavigate}
        className="px-3 py-1.5 text-sm text-text-dim hover:text-text transition-colors rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-text/40"
      >
        Sign in
      </Link>
    );
  }

  return (
    <span className="flex items-center gap-1">
      <Link
        href="/account"
        onClick={onNavigate}
        className="px-3 py-1.5 text-sm font-mono text-text-dim hover:text-text transition-colors rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-text/40 truncate max-w-[180px]"
        title={email}
      >
        {email}
      </Link>
      <form action="/auth/signout" method="post">
        <button
          type="submit"
          className="px-3 py-1.5 text-sm text-text-dim hover:text-text transition-colors rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-text/40"
        >
          Sign out
        </button>
      </form>
    </span>
  );
}
