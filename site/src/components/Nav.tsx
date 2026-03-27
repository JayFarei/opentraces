import Link from "next/link";
import Logo from "./Logo";

export default function Nav() {
  return (
    <nav className="nav">
      <Link href="/" className="nav-logo logo-mark">
        <Logo />
        opentraces<span className="dot">.</span>ai
      </Link>
      <div className="nav-links">
        <Link href="/schema" className="nav-link">schema</Link>
        <a href="#" className="nav-link">dashboard</a>
        <a href="#" className="nav-link">datasets</a>
        <a href="https://github.com/opentraces" className="nav-link" target="_blank" rel="noopener noreferrer">github</a>
        <span style={{ color: "var(--border)" }}>|</span>
        <a href="#" className="btn btn-outline btn-sm">[sign in]</a>
      </div>
    </nav>
  );
}
