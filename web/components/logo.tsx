/**
 * AlphaMolt mark — dark rounded square, white "A" path (font-independent),
 * green upward-arrow slashing through it. Same SVG geometry as app/icon.svg
 * so the favicon and the nav logo stay visually identical.
 */
interface LogoProps {
  size?: number;
  className?: string;
  title?: string;
}

export default function Logo({
  size = 28,
  className,
  title,
}: LogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {title && <title>{title}</title>}
      {/* Dark circle background — matches the original mark. */}
      <circle cx="32" cy="32" r="32" fill="#15171A" />
      {/* Bold sans-serif "A" inscribed in the circle — single closed
          path traced outside-then-inside so the inner counter is open
          at the bottom (Λ-shape). The green slash visually sits where
          a crossbar would be. */}
      <path
        fill="#EDEDED"
        d="M 12 52 L 29 12 L 35 12 L 52 52 L 45 52 L 37 30 L 27 30 L 19 52 Z"
      />
      {/* Green diagonal stroke (lower-left → upper-right). */}
      <line
        x1="15"
        y1="42"
        x2="45"
        y2="24"
        stroke="#00FF41"
        strokeWidth="4"
        strokeLinecap="round"
      />
      {/* Arrowhead — isoceles triangle aligned with the line direction.
          Same green as the line; overlap with the round cap is invisible
          because both shapes share the fill. */}
      <path d="M 51 21 L 41 20 L 46 29 Z" fill="#00FF41" />
    </svg>
  );
}
