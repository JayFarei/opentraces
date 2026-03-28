import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Schema - opentraces.ai",
  description: "Complete schema documentation for the opentraces.ai trace format.",
};

export default function SchemaLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
