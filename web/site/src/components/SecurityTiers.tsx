"use client";

import { useState } from "react";

const tiers = [
  {
    id: "explicit",
    name: "explicit tools",
    nameStyle: { color: "var(--accent)" },
    desc: "Workflows call security sanitize with a named tool list.",
    tag: null,
  },
  {
    id: "config",
    name: "config enabled",
    nameStyle: {},
    desc: "Only tools explicitly enabled in config run with --use-config.",
    tag: "opt-in",
  },
];

export default function SecurityTiers() {
  const [selected, setSelected] = useState("explicit");

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
            name="security-mode"
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
