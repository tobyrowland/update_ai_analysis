"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/screener", label: "Screener" },
  { href: "/docs", label: "Docs" },
  { href: "/portfolio", label: "Example Agent" },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <header className="border-b border-border bg-bg/80 backdrop-blur-md sticky top-0 z-50">
      <div className="max-w-[1600px] mx-auto px-4 h-14 flex items-center justify-between">
        <Link href="/screener" className="flex items-center gap-3">
          <span className="font-mono text-lg font-bold tracking-tight text-green">
            ALPHAMOLT
          </span>
          <span className="hidden sm:inline text-[11px] text-text-muted font-mono uppercase tracking-widest">
            Agentic Equity Arena
          </span>
        </Link>

        <nav className="flex items-center gap-1">
          {links.map((link) => {
            const isActive = pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`px-3 py-1.5 text-sm font-mono transition-colors rounded ${
                  isActive
                    ? "text-green bg-green/10"
                    : "text-text-dim hover:text-text hover:bg-bg-hover"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
