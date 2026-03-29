export default function Footer() {
  return (
    <footer>
      <span>&copy; {new Date().getFullYear()} opentraces contributors. MIT license.</span>
      <span><span className="brand-open">open</span><span className="brand-traces">traces</span></span>
    </footer>
  );
}
