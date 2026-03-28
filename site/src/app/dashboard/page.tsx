import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import Dashboard from "@/components/Dashboard";

export default function DashboardPage() {
  return (
    <>
      <div className="container">
        <Nav />
        <Dashboard />
        <Footer />
      </div>
    </>
  );
}
