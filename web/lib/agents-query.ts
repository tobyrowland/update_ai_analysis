/**
 * Supabase query logic for agents (Phase 2a.5).
 *
 * Mirrors the shape of equities-query.ts so both surfaces (REST v1 and
 * any future MCP tools) can share the same code path.
 */

import { getSupabase } from "@/lib/supabase";
import { generateApiKey, type GeneratedKey } from "@/lib/api-keys";

export interface Agent {
  id: string;
  handle: string;
  display_name: string;
  description: string;
  contact_email: string | null;
  api_key_prefix: string;
  is_house_agent: boolean;
  created_at: string;
  updated_at: string;
}

export interface PublicAgent {
  handle: string;
  display_name: string;
  description: string;
  is_house_agent: boolean;
  created_at: string;
}

/** Public columns — never includes api_key_hash or contact_email. */
const PUBLIC_COLUMNS =
  "handle, display_name, description, is_house_agent, created_at";

export const HANDLE_RE = /^[a-z][a-z0-9-]{2,31}$/;

export interface CreateAgentInput {
  handle: string;
  display_name: string;
  description?: string;
  contact_email?: string;
}

export interface CreateAgentResult {
  agent: PublicAgent;
  api_key: string; // plaintext — caller must show and discard
}

export class AgentValidationError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export async function listPublicAgents(limit = 50): Promise<PublicAgent[]> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("agents")
    .select(PUBLIC_COLUMNS)
    .order("is_house_agent", { ascending: false })
    .order("created_at", { ascending: true })
    .limit(limit);
  if (error) throw new Error(`Supabase query failed: ${error.message}`);
  return (data ?? []) as PublicAgent[];
}

export async function countAgents(): Promise<number> {
  const supabase = getSupabase();
  const { count, error } = await supabase
    .from("agents")
    .select("*", { count: "exact", head: true });
  if (error) throw new Error(`Supabase query failed: ${error.message}`);
  return count ?? 0;
}

export async function createAgent(
  input: CreateAgentInput,
): Promise<CreateAgentResult> {
  const handle = input.handle.trim().toLowerCase();
  const display_name = input.display_name.trim();
  const description = (input.description ?? "").trim();
  const contact_email = input.contact_email?.trim() || null;

  if (!HANDLE_RE.test(handle)) {
    throw new AgentValidationError(
      "invalid_handle",
      "Handle must be 3-32 chars, lowercase alphanumeric + hyphens, starting with a letter.",
    );
  }
  if (!display_name) {
    throw new AgentValidationError(
      "invalid_display_name",
      "Display name is required.",
    );
  }
  if (display_name.length > 80) {
    throw new AgentValidationError(
      "invalid_display_name",
      "Display name must be 80 characters or fewer.",
    );
  }
  if (description.length > 500) {
    throw new AgentValidationError(
      "invalid_description",
      "Description must be 500 characters or fewer.",
    );
  }
  if (contact_email) {
    // Minimal shape check — not a full RFC validator on purpose.
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact_email)) {
      throw new AgentValidationError(
        "invalid_email",
        "Contact email is not a valid email address.",
      );
    }
    if (contact_email.length > 200) {
      throw new AgentValidationError(
        "invalid_email",
        "Contact email must be 200 characters or fewer.",
      );
    }
  }

  const key: GeneratedKey = generateApiKey();

  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("agents")
    .insert({
      handle,
      display_name,
      description,
      contact_email,
      api_key_hash: key.hash,
      api_key_prefix: key.prefix,
      is_house_agent: false,
    })
    .select(PUBLIC_COLUMNS)
    .single();

  if (error) {
    if (error.code === "23505") {
      throw new AgentValidationError(
        "handle_taken",
        `Handle '${handle}' is already taken.`,
      );
    }
    if (error.code === "23514") {
      throw new AgentValidationError(
        "invalid_handle",
        "Handle must be 3-32 chars, lowercase alphanumeric + hyphens, starting with a letter.",
      );
    }
    throw new Error(`Supabase insert failed: ${error.message}`);
  }

  return { agent: data as PublicAgent, api_key: key.plaintext };
}
