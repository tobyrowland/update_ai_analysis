"use client";

import { useState } from "react";
import { login } from "./action";

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(formData: FormData) {
    setLoading(true);
    setError(null);
    const result = await login(formData);
    if (result?.error) {
      setError(result.error);
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center">
      <div className="w-full max-w-sm mx-4">
        <div className="glass-card rounded-lg p-8">
          <div className="text-center mb-8">
            <h1 className="font-mono text-2xl font-bold tracking-tight text-green mb-2">
              ALPHAMOLT
            </h1>
            <p className="text-xs font-mono text-text-muted uppercase tracking-widest">
              System Authentication Required
            </p>
          </div>

          <form action={handleSubmit}>
            <div className="mb-4">
              <label
                htmlFor="password"
                className="block text-xs font-mono text-text-dim mb-2 uppercase tracking-wider"
              >
                Access Key
              </label>
              <input
                type="password"
                id="password"
                name="password"
                autoFocus
                required
                className="w-full bg-bg border border-border-light rounded px-3 py-2.5 font-mono text-sm text-green focus:outline-none focus:border-green/50 focus:ring-1 focus:ring-green/20 placeholder:text-text-muted"
                placeholder="Enter access key..."
              />
            </div>

            {error && (
              <p className="text-red text-xs font-mono mb-4">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-green/10 border border-green/30 text-green font-mono text-sm py-2.5 rounded hover:bg-green/20 transition-colors disabled:opacity-50"
            >
              {loading ? "AUTHENTICATING..." : "AUTHENTICATE"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
