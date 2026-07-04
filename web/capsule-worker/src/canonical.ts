// Canonical JSON serialization matching Python `json.dumps(obj, sort_keys=True,
// indent=2, ensure_ascii=True)` — the exact serialization the opentraces CLI
// uses when it writes `capsule.json` (core/capsule/share.py::write_capsule_dir)
// and prints `capsule open --json`.
//
// Byte-for-byte parity with that serializer is what lets the worker's JSON
// endpoints be "byte-identical to the CLI envelope for the carried core"
// (issue #199 acceptance). The parity is proven in tests against the frozen
// `sample-capsule.json`, which is itself stored in this canonical form.
//
// Python behaviour reproduced (verified empirically against CPython 3.10):
//   - object keys sorted by code point
//   - two-space indent, `": "` / `,` separators
//   - empty containers render inline as `[]` / `{}` (no interior newline)
//   - strings escaped with ensure_ascii: a char is emitted raw only when its
//     code unit is in 0x20..0x7e and is not `"` or `\`; everything else is
//     escaped (short forms for \b \t \n \f \r, otherwise \uXXXX). Iterating by
//     UTF-16 code unit makes astral chars emit surrogate-pair escapes, matching
//     Python's output exactly.

const SHORT_ESCAPES: Record<number, string> = {
  0x08: "\\b",
  0x09: "\\t",
  0x0a: "\\n",
  0x0c: "\\f",
  0x0d: "\\r",
  0x22: '\\"',
  0x5c: "\\\\",
};

function encodeString(value: string): string {
  let out = '"';
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    const short = SHORT_ESCAPES[code];
    if (short !== undefined) {
      out += short;
    } else if (code >= 0x20 && code <= 0x7e) {
      out += value[i];
    } else {
      out += "\\u" + code.toString(16).padStart(4, "0");
    }
  }
  return out + '"';
}

function encodeNumber(value: number): string {
  if (!Number.isFinite(value)) {
    // Python emits Infinity/NaN literals; capsule envelopes never carry them.
    // Refuse rather than silently diverge.
    throw new Error(`canonicalStringify: non-finite number ${value}`);
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return String(value);
}

function encode(value: unknown, indent: number): string {
  if (value === null) return "null";
  const t = typeof value;
  if (t === "string") return encodeString(value as string);
  if (t === "number") return encodeNumber(value as number);
  if (t === "boolean") return value ? "true" : "false";

  const childPad = " ".repeat(indent + 2);
  const closePad = " ".repeat(indent);

  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    const items = value.map((item) => childPad + encode(item, indent + 2));
    return "[\n" + items.join(",\n") + "\n" + closePad + "]";
  }

  if (t === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    if (keys.length === 0) return "{}";
    const items = keys.map(
      (key) => childPad + encodeString(key) + ": " + encode(obj[key], indent + 2),
    );
    return "{\n" + items.join(",\n") + "\n" + closePad + "}";
  }

  throw new Error(`canonicalStringify: unsupported value of type ${t}`);
}

/**
 * Serialize a value to JSON byte-identical to Python's
 * `json.dumps(value, sort_keys=True, indent=2)`.
 */
export function canonicalStringify(value: unknown): string {
  return encode(value, 0);
}
