"use client";

import { useState } from "react";

const tiers = [
  {
    id: "auto",
    name: "auto",
    nameStyle: { color: "var(--accent)" },
    desc: "Scan, redact, and push automatically. For open-source and personal projects.",
    tag: null,
  },
  {
    id: "review",
    name: "review",
    nameStyle: {},
    desc: "Review and approve every trace before pushing. Nothing leaves without approval.",
    tag: "default",
  },
];

export default function SecurityTiers() {
  const [selected, setSelected] = useState("review");

  return (
    <div>
      {tiers.map((t) => (
        <label
          key={t.id}
          className={`tier${selected === t.id ? " active" : ""}`}
          onClick={() => setSelected(t.id)}
        >
          <input
            type="radio"
            name="tier"
            checked={selected === t.id}
            onChange={() => setSelected(t.id)}
          />
          <div>
            <div className="tier-name" style={t.nameStyle}>
              {t.name}
              {t.tag && <span className="tier-tag">{t.tag}</span>}
            </div>
            <div className="tier-desc">{t.desc}</div>
          </div>
        </label>
      ))}
    </div>
  );
}
