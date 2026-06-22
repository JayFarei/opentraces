// Renders a JSON-LD <script> for structured data (schema.org). Server-safe.
// Pass any schema.org object (or a @graph) and it is serialized into the
// document so search and AI answer engines can read machine-readable facts.
export default function JsonLd({ data }: { data: Record<string, unknown> }) {
  return (
    <script
      type="application/ld+json"
      // JSON.stringify output is safe here: it is our own structured data, not
      // user input. The </script> escape guards against any stray sequence.
      dangerouslySetInnerHTML={{
        __html: JSON.stringify(data).replace(/</g, "\\u003c"),
      }}
    />
  );
}
