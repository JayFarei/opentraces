import Link from "next/link";

export default function Nav() {
  return (
    <nav className="nav">
      <Link href="/" className="nav-logo">
        <span className="brand-open">open</span><span className="brand-traces">traces</span>
      </Link>
      <div className="nav-links">
        <Link href="/schema" className="nav-link">schema</Link>
        <Link href="/dashboard" className="nav-link">dashboard</Link>
        <Link href="/docs" className="nav-link">docs</Link>
        <a href="/llms.txt" className="nav-link">/llms.txt</a>
        <a href="https://github.com/opentraces" className="nav-link" target="_blank" rel="noopener noreferrer">github</a>
      </div>
    </nav>
  );
}
