import type { NextConfig } from "next";
import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { createHash } from "crypto";
import { resolve, dirname } from "path";

// Read version from Python package at config-load time (before bundling)
let version = "0.0.0";
try {
  const initPy = readFileSync(resolve(__dirname, "../../src/opentraces/__init__.py"), "utf-8");
  const match = initPy.match(/__version__\s*=\s*"([^"]+)"/);
  if (match) version = match[1];
} catch {}
writeFileSync(resolve(__dirname, "src/lib/version.json"), JSON.stringify({ version }));

// Generate Agent Skills discovery index (RFC v0.2.0) from skill/SKILL.md
try {
  const skillSrc = resolve(__dirname, "../../skill/SKILL.md");
  const skillContent = readFileSync(skillSrc, "utf-8");
  const fm = skillContent.match(/^---\n([\s\S]*?)\n---/);
  let skillName = "opentraces";
  let skillDescription = "";
  if (fm) {
    const nameMatch = fm[1].match(/^name:\s*(.+)$/m);
    if (nameMatch) skillName = nameMatch[1].trim();
    const descBlock = fm[1].match(/description:\s*>\s*\n([\s\S]*?)(?=\n\w|$)/);
    if (descBlock) {
      skillDescription = descBlock[1].split("\n").map((l) => l.trim()).filter(Boolean).join(" ");
    } else {
      const descLine = fm[1].match(/^description:\s*(.+)$/m);
      if (descLine) skillDescription = descLine[1].trim();
    }
  }
  const publicSkillPath = resolve(__dirname, `public/.well-known/agent-skills/${skillName}/SKILL.md`);
  mkdirSync(dirname(publicSkillPath), { recursive: true });
  writeFileSync(publicSkillPath, skillContent);
  const sha256 = createHash("sha256").update(skillContent).digest("hex");
  const index = {
    $schema: "https://agentskills.io/schemas/v0.2.0/index.json",
    skills: [
      {
        name: skillName,
        type: "skill",
        description: skillDescription,
        url: `https://opentraces.ai/.well-known/agent-skills/${skillName}/SKILL.md`,
        sha256,
      },
    ],
  };
  writeFileSync(
    resolve(__dirname, "public/.well-known/agent-skills/index.json"),
    JSON.stringify(index, null, 2) + "\n",
  );
} catch (err) {
  console.warn("[next.config] Skipping agent-skills index generation:", err);
}

const nextConfig: NextConfig = {
  transpilePackages: ["@opentraces/ui"],
  allowedDevOrigins: ["gabrieles-mac-mini-1.taila1b059.ts.net"],
  async headers() {
    const linkHeader = [
      '</docs>; rel="service-doc"; type="text/html"',
      '</llms.txt>; rel="describedby"; type="text/plain"',
      '</schema>; rel="describedby"; type="text/html"',
      '<https://github.com/jayfarei/opentraces>; rel="via"',
    ].join(", ");
    return [
      {
        source: "/",
        headers: [{ key: "Link", value: linkHeader }],
      },
      {
        source: "/.well-known/http-message-signatures-directory",
        headers: [
          { key: "Content-Type", value: "application/http-message-signatures-directory+json" },
          { key: "Cache-Control", value: "public, max-age=3600" },
        ],
      },
      {
        source: "/.well-known/agent-skills/:path*/SKILL.md",
        headers: [{ key: "Content-Type", value: "text/markdown; charset=utf-8" }],
      },
    ];
  },
};

export default nextConfig;
