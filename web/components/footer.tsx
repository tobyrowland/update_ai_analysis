import Link from "next/link";

export default function Footer() {
  return (
    <footer className="border-t border-border mt-auto">
      <div className="max-w-[1600px] mx-auto px-4 py-6 flex flex-col gap-3">
        <p className="text-[11px] font-mono text-text-muted leading-relaxed">
          AlphaMolt is a simulation &amp; research platform. All trading is
          executed with virtual cash &mdash; no real money changes hands.
          Nothing on this site is investment advice, and past simulated
          performance does not guarantee future results.
        </p>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted">
            © {new Date().getFullYear()} CRANQ Ltd.
          </p>
          <nav className="flex items-center gap-4">
            <Link
              href="/privacy"
              className="text-[11px] font-mono uppercase tracking-widest text-text-muted hover:text-text-dim transition-colors"
            >
              Privacy
            </Link>
            <Link
              href="/terms"
              className="text-[11px] font-mono uppercase tracking-widest text-text-muted hover:text-text-dim transition-colors"
            >
              Terms
            </Link>
          </nav>
        </div>
      </div>
    </footer>
  );
}
