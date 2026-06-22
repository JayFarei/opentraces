"use client";

import { useRouter } from "next/navigation";

// Interactive version picker for the schema reference. Extracted from the page
// so the page itself can stay a server component (per-version metadata + SSG).
export default function SchemaVersionSelect({
  displaySlug,
  latestVersion,
  versions,
  date,
}: {
  displaySlug: string;
  latestVersion: string;
  versions: { version: string }[];
  date: string;
}) {
  const router = useRouter();

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        background: "var(--bg-alt)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: "4px 10px 4px 12px",
      }}
    >
      <select
        value={displaySlug}
        onChange={(e) => router.push(`/schema/${e.target.value}`)}
        style={{
          background: "transparent",
          color: "var(--accent)",
          border: "none",
          fontSize: 14,
          fontFamily: "var(--font-mono)",
          fontWeight: 500,
          cursor: "pointer",
          outline: "none",
          appearance: "none",
          WebkitAppearance: "none",
          paddingRight: 16,
          backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%239A9895' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")`,
          backgroundRepeat: "no-repeat",
          backgroundPosition: "right 0 center",
        }}
      >
        <option value="latest">latest (v{latestVersion})</option>
        {versions.map((v) => (
          <option key={v.version} value={v.version}>
            v{v.version}
          </option>
        ))}
      </select>

      <span style={{ width: 1, height: 16, background: "var(--border)", flexShrink: 0 }} />

      <span style={{ fontSize: 11, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{date}</span>
    </div>
  );
}
