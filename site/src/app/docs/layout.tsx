import ThemeToggle from "@/components/ThemeToggle";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import DocsSidebar from "@/components/DocsSidebar";

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ThemeToggle />
      <div className="container">
        <Nav />
        <div className="docs-layout">
          <DocsSidebar />
          <main className="docs-main">
            {children}
          </main>
        </div>
        <Footer />
      </div>
    </>
  );
}
