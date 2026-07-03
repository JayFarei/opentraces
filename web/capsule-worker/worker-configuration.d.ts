// Cloudflare Workers runtime types. This worker declares no bindings beyond the
// `CORS_ORIGIN` var (see the `Env` interface exported from `src/index.ts`), so
// there is nothing generated to vendor here — we only pull in the global
// runtime types (fetch, Request, Response, ExportedHandler, crypto.subtle, …).
/// <reference types="@cloudflare/workers-types" />
