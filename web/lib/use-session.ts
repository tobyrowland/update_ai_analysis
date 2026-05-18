"use client";

import { useEffect, useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * Client-side Supabase session email, shared by the nav components.
 *
 * Resolved after mount so every page that renders <Nav /> stays
 * static/ISR — a server-side session read would force them all into
 * dynamic rendering. `ready` is false until the first resolution, so
 * callers can avoid flashing the wrong auth state.
 */
export function useSessionEmail(): { email: string | null; ready: boolean } {
  const [email, setEmail] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const supabase = createSupabaseBrowserClient();
    supabase.auth.getSession().then(({ data }) => {
      setEmail(data.session?.user.email ?? null);
      setReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setEmail(session?.user.email ?? null);
      setReady(true);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  return { email, ready };
}
