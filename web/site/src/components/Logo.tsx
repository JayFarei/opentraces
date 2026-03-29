export default function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg
      className="logo-glyph"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
    >
      <ellipse
        cx="16" cy="16" rx="9" ry="5.5"
        transform="rotate(45,16,16)"
        fill="none" stroke="var(--text)" strokeWidth="3.4" strokeLinecap="round"
      />
      <ellipse
        cx="16" cy="16" rx="9" ry="5.5"
        transform="rotate(-45,16,16)"
        fill="none" stroke="var(--accent)" strokeWidth="3.4" strokeLinecap="round"
      />
      <path
        d="M 13.5,8.5 C 15,9.2 17,10 18.7,11"
        stroke="var(--text)" strokeWidth="3.4" fill="none" strokeLinecap="round"
      />
      <path
        d="M 18.5,23.5 C 17,22.8 15,22 13.3,21"
        stroke="var(--text)" strokeWidth="3.4" fill="none" strokeLinecap="round"
      />
    </svg>
  );
}
