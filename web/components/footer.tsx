export default function Footer() {
  return (
    <footer className="mt-auto border-t border-border bg-bg/80 backdrop-blur-md">
      <div className="max-w-[1600px] mx-auto px-4 py-6">
        <p className="text-[11px] font-mono text-text-muted leading-relaxed">
          <span className="uppercase tracking-widest text-text-dim">
            Disclaimer —{" "}
          </span>
          Not investment advice. This site and the information therein is
          provided for research purposes only. We have worked hard to ensure
          the veracity &amp; currency of the data in this site, but we do not
          guarantee it, and are not liable for its accuracy.
        </p>
      </div>
    </footer>
  );
}
