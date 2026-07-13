import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";
import { createHash } from "node:crypto";

const HOST = "127.0.0.1";
const PORT = Number.parseInt(process.env.PORT ?? "4318", 10);
const LEDGER_PATH = process.env.LEDGER_PATH;

const operations = [
  { operationId: "createRepo", method: "POST", path: "/api/repos/create", status: "hand-authored" },
  { operationId: "datasetInfo", method: "GET", path: "/api/datasets/{id}", status: "hand-authored" },
  { operationId: "listRepoTree", method: "GET", path: "/api/datasets/{id}/tree/{rev}", status: "hand-authored" },
  { operationId: "resolveFile", method: "GET", path: "/datasets/{id}/resolve/{rev}/{path}", status: "hand-authored" },
  {
    operationId: "preupload",
    method: "POST",
    path: "/api/datasets/{id}/preupload/{rev}",
    status: "hand-authored",
    summary: "forces regular upload mode",
  },
  { operationId: "commit", method: "POST", path: "/api/datasets/{id}/commit/{rev}", status: "hand-authored" },
  { operationId: "whoami", method: "GET", path: "/api/whoami-v2", status: "hand-authored" },
  { operationId: "updateSettings", method: "PUT", path: "/api/datasets/{id}/settings", status: "hand-authored" },
  { operationId: "listDatasets", method: "GET", path: "/api/datasets", status: "partial" },
  { operationId: "deleteRepo", method: "DELETE", path: "/api/repos/delete", status: "partial" },
  { operationId: "lfsBatch", method: "POST", path: "/{id}.git/info/lfs/objects/batch", status: "unsupported" },
  { operationId: "xetUpload", status: "unsupported" },
] as const;

const manifest = {
  id: "huggingface",
  name: "Hugging Face Hub",
  surfaces: [
    { id: "hub-rest", kind: "rest", title: "Hub REST API", basePath: "/api", status: "partial" },
    { id: "resolve", kind: "provider-specific", title: "File resolve/download", status: "partial" },
  ],
  auth: [{ id: "user-token", title: "User access token", type: "bearer-token", status: "supported" }],
  specs: [{ kind: "manual", title: "Hand-authored Hub emulator", coverage: "partial", operations }],
  seedSchema: { repos: [] },
  stateModel: { collections: [{ name: "hf.repos" }, { name: "hf.files" }, { name: "hf.commits" }] },
  connections: [
    {
      id: "hf-endpoint",
      title: "Point client at emulator",
      kind: "env",
      language: "bash",
      template: "HF_ENDPOINT={{baseUrl}}\nHF_TOKEN={{token}}",
    },
  ],
};

let requestSequence =
  LEDGER_PATH !== undefined && existsSync(LEDGER_PATH)
    ? readFileSync(LEDGER_PATH, "utf8").split("\n").filter(Boolean).length
    : 0;

type Repo = {
  repoId: string;
  private: boolean;
  gated: boolean | "auto" | "manual";
  tags: string[];
  headOid: string;
  createdAt: string;
  updatedAt: string;
};

const repos = new Map<string, Repo>();

type StoredFile = {
  content: Uint8Array;
  oid: string;
  revOid: string;
};

const files = new Map<string, Map<string, StoredFile>>();

function appendLedger(
  request: Request,
  operationId: string,
  status: number,
  requestPayload: unknown = null,
  responsePayload: Record<string, unknown> = {},
): void {
  if (LEDGER_PATH === undefined) return;
  mkdirSync(dirname(LEDGER_PATH), { recursive: true });
  requestSequence += 1;
  appendFileSync(
    LEDGER_PATH,
    `${JSON.stringify({
      request_id: `req_${requestSequence}`,
      observed_at: new Date().toISOString(),
      method: request.method,
      path: new URL(request.url).pathname,
      operation_id: operationId,
      request: requestPayload,
      response: { status, ...responsePayload },
    })}\n`,
    { encoding: "utf8", flag: "a" },
  );
}

function jsonResponse(payload: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return Response.json(payload, { status, headers });
}

function errorResponse(errorCode: string, message: string, status: number): Response {
  return jsonResponse({ error: message }, status, { "X-Error-Code": errorCode });
}

function datasetInfo(repo: Repo): Record<string, unknown> {
  return {
    id: repo.repoId,
    author: repo.repoId.split("/")[0],
    sha: repo.headOid,
    private: repo.private,
    gated: repo.gated,
    disabled: false,
    tags: repo.tags,
    downloads: 0,
    likes: 0,
    createdAt: repo.createdAt,
    lastModified: repo.updatedAt,
    siblings: [],
  };
}

function revisionExists(repo: Repo, revision: string): boolean {
  const decoded = decodeURIComponent(revision);
  return decoded === "main" || decoded === repo.headOid;
}

Bun.serve({
  hostname: HOST,
  port: PORT,
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (request.method === "GET" && path === "/_emulate/manifest") {
      appendLedger(request, "manifest", 200);
      return jsonResponse(manifest);
    }
    if (request.method === "GET" && path === "/_emulate/ledger") {
      const content =
        LEDGER_PATH !== undefined && existsSync(LEDGER_PATH)
          ? readFileSync(LEDGER_PATH, "utf8")
          : "";
      return new Response(content, {
        status: 200,
        headers: { "Content-Type": "application/x-ndjson" },
      });
    }
    if (request.method === "POST" && path === "/api/repos/create") {
      const body = (await request.json()) as Record<string, unknown>;
      const name = String(body.name ?? "");
      const organization = body.organization ? String(body.organization) : "bench";
      const repoId = `${organization}/${name}`;
      if (repos.has(repoId)) {
        appendLedger(request, "createRepo", 409);
        return jsonResponse({ error: "repository already exists" }, 409);
      }
      const now = new Date().toISOString();
      repos.set(repoId, {
        repoId,
        private: body.visibility === "private" || Boolean(body.private),
        gated: false,
        tags: [],
        headOid: "0".repeat(40),
        createdAt: now,
        updatedAt: now,
      });
      appendLedger(request, "createRepo", 200, body, { repo_id: repoId });
      return jsonResponse({ url: `http://${HOST}:${PORT}/datasets/${repoId}` });
    }
    if (request.method === "GET" && path === "/api/whoami-v2") {
      const authorization = request.headers.get("authorization") ?? "";
      if (!authorization.startsWith("Bearer hf_")) {
        appendLedger(request, "whoami", 401);
        return errorResponse("InvalidToken", "invalid access token", 401);
      }
      appendLedger(request, "whoami", 200);
      return jsonResponse({ name: "bench", type: "user" });
    }
    if (request.method === "GET" && path === "/api/datasets") {
      const author = new URL(request.url).searchParams.get("author");
      const result = [...repos.values()]
        .filter((repo) => author === null || repo.repoId.startsWith(`${author}/`))
        .map(datasetInfo);
      appendLedger(request, "listDatasets", 200);
      return jsonResponse(result);
    }
    if (request.method === "DELETE" && path === "/api/repos/delete") {
      const body = (await request.json()) as Record<string, unknown>;
      const name = String(body.name ?? "");
      const organization = body.organization ? String(body.organization) : "bench";
      const repoId = `${organization}/${name}`;
      if (!repos.delete(repoId)) {
        appendLedger(request, "deleteRepo", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      appendLedger(request, "deleteRepo", 200);
      return jsonResponse({ ok: true });
    }
    const settingsMatch = path.match(/^\/api\/datasets\/([^/]+\/[^/]+)\/settings$/);
    if (request.method === "PUT" && settingsMatch !== null) {
      const repo = repos.get(decodeURIComponent(settingsMatch[1]));
      if (repo === undefined) {
        appendLedger(request, "updateSettings", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      const body = (await request.json()) as Record<string, unknown>;
      if (body.visibility !== undefined) repo.private = body.visibility === "private";
      if (body.private !== undefined) repo.private = Boolean(body.private);
      if (body.gated !== undefined) repo.gated = body.gated as Repo["gated"];
      if (Array.isArray(body.tags)) repo.tags = body.tags.map(String);
      repo.updatedAt = new Date().toISOString();
      appendLedger(request, "updateSettings", 200);
      return jsonResponse({ ok: true });
    }
    const preuploadMatch = path.match(
      /^\/api\/datasets\/([^/]+\/[^/]+)\/preupload\/([^/]+)$/,
    );
    if (request.method === "POST" && preuploadMatch !== null) {
      const repoId = decodeURIComponent(preuploadMatch[1]);
      if (!repos.has(repoId)) {
        appendLedger(request, "preupload", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      const body = (await request.json()) as {
        files?: Array<{ path: string }>;
      };
      const repoFiles = files.get(repoId) ?? new Map<string, StoredFile>();
      const responseFiles = (body.files ?? []).map((file) => ({
        path: file.path,
        uploadMode: "regular",
        shouldIgnore: false,
        oid: repoFiles.get(file.path)?.oid,
      }));
      appendLedger(request, "preupload", 200, {
        repo_id: repoId,
        revision: decodeURIComponent(preuploadMatch[2]),
        files: responseFiles.map((file) => file.path),
      });
      return jsonResponse({ files: responseFiles });
    }
    const commitMatch = path.match(
      /^\/api\/datasets\/([^/]+\/[^/]+)\/commit\/([^/]+)$/,
    );
    if (request.method === "POST" && commitMatch !== null) {
      const repoId = decodeURIComponent(commitMatch[1]);
      const repo = repos.get(repoId);
      if (repo === undefined) {
        appendLedger(request, "commit", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      const rawPayload = await request.text();
      const items = rawPayload
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line) as { key: string; value: Record<string, unknown> });
      const repoFiles = files.get(repoId) ?? new Map<string, StoredFile>();
      const oid = createHash("sha1")
        .update(repo.headOid)
        .update(rawPayload)
        .digest("hex");
      for (const item of items) {
        if (item.key === "file") {
          const filePath = String(item.value.path);
          const content = Buffer.from(String(item.value.content), "base64");
          repoFiles.set(filePath, {
            content,
            oid: createHash("sha1").update(content).digest("hex"),
            revOid: oid,
          });
        } else if (item.key === "deletedFile") {
          repoFiles.delete(String(item.value.path));
        } else if (item.key === "deletedFolder") {
          const prefix = `${String(item.value.path).replace(/\/$/, "")}/`;
          for (const filePath of repoFiles.keys()) {
            if (filePath.startsWith(prefix)) repoFiles.delete(filePath);
          }
        }
      }
      files.set(repoId, repoFiles);
      repo.headOid = oid;
      repo.updatedAt = new Date().toISOString();
      appendLedger(
        request,
        "commit",
        200,
        {
          repo_id: repoId,
          revision: decodeURIComponent(commitMatch[2]),
          files: items
            .filter((item) => item.key === "file")
            .map((item) => String(item.value.path)),
        },
        { commit_oid: oid },
      );
      return jsonResponse({
        commitOid: oid,
        commitUrl: `http://${HOST}:${PORT}/datasets/${repoId}/commit/${oid}`,
      });
    }
    const treeMatch = path.match(
      /^\/api\/datasets\/([^/]+\/[^/]+)\/tree\/([^/]+)(?:\/(.*))?$/,
    );
    if (request.method === "GET" && treeMatch !== null) {
      const repoId = decodeURIComponent(treeMatch[1]);
      const repo = repos.get(repoId);
      if (repo === undefined) {
        appendLedger(request, "listRepoTree", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      if (!revisionExists(repo, treeMatch[2])) {
        appendLedger(request, "listRepoTree", 404);
        return errorResponse("RevisionNotFound", "revision not found", 404);
      }
      const prefix = treeMatch[3] ? `${decodeURIComponent(treeMatch[3]).replace(/\/$/, "")}/` : "";
      const result = [...(files.get(repoId) ?? new Map()).entries()]
        .filter(([filePath]) => filePath.startsWith(prefix))
        .map(([filePath, file]) => ({
          type: "file",
          path: filePath,
          size: file.content.byteLength,
          oid: file.oid,
        }));
      appendLedger(request, "listRepoTree", 200);
      return jsonResponse(result);
    }
    const resolveMatch = path.match(
      /^\/(?:datasets\/)?([^/]+\/[^/]+)\/resolve\/([^/]+)\/(.+)$/,
    );
    if ((request.method === "HEAD" || request.method === "GET") && resolveMatch !== null) {
      const repoId = decodeURIComponent(resolveMatch[1]);
      const repo = repos.get(repoId);
      if (repo === undefined) {
        appendLedger(request, "resolveFile", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      if (!revisionExists(repo, resolveMatch[2])) {
        appendLedger(request, "resolveFile", 404);
        return errorResponse("RevisionNotFound", "revision not found", 404);
      }
      const filePath = decodeURIComponent(resolveMatch[3]);
      const file = files.get(repoId)?.get(filePath);
      if (file === undefined) {
        appendLedger(request, "resolveFile", 404);
        return errorResponse("EntryNotFound", "file not found", 404);
      }
      const headers = {
        "Content-Length": String(file.content.byteLength),
        "Content-Type": "application/octet-stream",
        ETag: `\"${file.oid}\"`,
        "X-Repo-Commit": repo.headOid,
      };
      appendLedger(request, "resolveFile", 200);
      return new Response(request.method === "HEAD" ? null : file.content, { status: 200, headers });
    }
    const datasetInfoMatch = path.match(/^\/api\/datasets\/([^/]+\/[^/]+)$/);
    if (request.method === "GET" && datasetInfoMatch !== null) {
      const repo = repos.get(decodeURIComponent(datasetInfoMatch[1]));
      if (repo === undefined) {
        appendLedger(request, "datasetInfo", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      appendLedger(request, "datasetInfo", 200);
      return jsonResponse(datasetInfo(repo));
    }
    appendLedger(request, "unmatched", 404);
    return jsonResponse(
      { error: "not found" },
      404,
      { "X-Error-Code": "EntryNotFound" },
    );
  },
});

console.log(`huggingface emulator listening on http://${HOST}:${PORT}`);
