import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";
import { createHash } from "node:crypto";

const HOST = "127.0.0.1";
const PORT = Number.parseInt(process.env.PORT ?? "14318", 10);
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
  {
    operationId: "validateYaml",
    method: "POST",
    path: "/api/validate-yaml",
    status: "hand-authored",
    summary: "validates the dataset card before folder upload",
  },
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
  auth: [
    {
      id: "user-token",
      title: "User access token",
      type: "bearer-token",
      status: "supported",
      validation: "minted-or-baseline-state",
    },
  ],
  specs: [{ kind: "manual", title: "Hand-authored Hub emulator", coverage: "partial", operations }],
  controlPlane: {
    credentials: "POST /_emulate/credentials",
    seed: "POST /_emulate/seed",
    reset: "POST /_emulate/reset",
  },
  seedSchema: {
    repos: [{ repo_id: "namespace/name", private: false, gated: false, tags: [] }],
  },
  stateModel: {
    collections: [
      { name: "hf.credentials" },
      { name: "hf.repos" },
      { name: "hf.files" },
      { name: "hf.revisions" },
    ],
  },
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

type FileSet = Map<string, StoredFile>;

const files = new Map<string, FileSet>();
const revisions = new Map<string, Map<string, FileSet>>();

type Identity = {
  name: string;
  type: "user";
};

const BASELINE_TOKEN = "hf_bench_user_token";
const credentials = new Map<string, Identity>();

function resetCredentials(): void {
  credentials.clear();
  credentials.set(BASELINE_TOKEN, { name: "bench", type: "user" });
}

resetCredentials();

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

function authenticatedIdentity(request: Request): Identity | undefined {
  const authorization = request.headers.get("authorization") ?? "";
  if (!authorization.startsWith("Bearer ")) return undefined;
  return credentials.get(authorization.slice("Bearer ".length));
}

function requireAuthentication(request: Request, operationId: string): Response | undefined {
  if (authenticatedIdentity(request) !== undefined) return undefined;
  appendLedger(request, operationId, 401);
  return errorResponse("InvalidToken", "invalid access token", 401);
}

function ownsRepo(identity: Identity | undefined, repoId: string): boolean {
  return identity !== undefined && identity.name === repoId.split("/", 1)[0];
}

function requireRepoOwnership(
  request: Request,
  operationId: string,
  repoId: string,
): Response | undefined {
  const authenticationError = requireAuthentication(request, operationId);
  if (authenticationError !== undefined) return authenticationError;
  if (ownsRepo(authenticatedIdentity(request), repoId)) return undefined;
  appendLedger(request, operationId, 403);
  return errorResponse("Forbidden", "repository write access denied", 403);
}

function requireRepoReadAccess(
  request: Request,
  operationId: string,
  repo: Repo,
): Response | undefined {
  const authorization = request.headers.get("authorization");
  const identity = authenticatedIdentity(request);
  if (authorization !== null && identity === undefined) {
    appendLedger(request, operationId, 401);
    return errorResponse("InvalidToken", "invalid access token", 401);
  }
  if (
    (repo.private || repo.gated !== false) &&
    identity !== undefined &&
    !ownsRepo(identity, repo.repoId)
  ) {
    appendLedger(request, operationId, 404);
    return errorResponse("RepoNotFound", "repository not found", 404);
  }
  if (repo.private && identity === undefined) {
    appendLedger(request, operationId, 401);
    return errorResponse("InvalidToken", "authentication required", 401);
  }
  if (repo.gated !== false && identity === undefined) {
    appendLedger(request, operationId, 403);
    return errorResponse("GatedRepo", "gated repository access denied", 403);
  }
  return undefined;
}

function newRepo(
  repoId: string,
  options: {
    private?: boolean;
    gated?: boolean | "auto" | "manual";
    tags?: string[];
  } = {},
): Repo {
  const now = new Date().toISOString();
  const repo = {
    repoId,
    private: options.private ?? false,
    gated: options.gated ?? false,
    tags: options.tags ?? [],
    headOid: "0".repeat(40),
    createdAt: now,
    updatedAt: now,
  };
  repos.set(repoId, repo);
  const initialFiles: FileSet = new Map();
  files.set(repoId, initialFiles);
  revisions.set(repoId, new Map([[repo.headOid, new Map(initialFiles)]]));
  return repo;
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

function filesAtRevision(repo: Repo, revision: string): FileSet | undefined {
  const decoded = decodeURIComponent(revision);
  if (decoded === "main") return files.get(repo.repoId);
  return revisions.get(repo.repoId)?.get(decoded);
}

function revisionExists(repo: Repo, revision: string): boolean {
  return filesAtRevision(repo, revision) !== undefined;
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
    if (request.method === "POST" && path === "/api/validate-yaml") {
      const unauthorized = requireAuthentication(request, "validateYaml");
      if (unauthorized) return unauthorized;
      const body = (await request.json()) as Record<string, unknown>;
      const content = String(body.content ?? "");
      const observed = {
        repo_type: String(body.repoType ?? "model"),
        content_sha256: createHash("sha256").update(content).digest("hex"),
      };
      appendLedger(request, "validateYaml", 200, observed);
      return jsonResponse({ warnings: [], errors: [] });
    }
    if (request.method === "POST" && path === "/_emulate/credentials") {
      const body = (await request.json()) as Record<string, unknown>;
      const name = String(body.name ?? "bench");
      const token =
        name === "bench"
          ? BASELINE_TOKEN
          : `hf_bench_${createHash("sha256").update(name).digest("hex").slice(0, 24)}`;
      const identity: Identity = { name, type: "user" };
      credentials.set(token, identity);
      appendLedger(request, "mintCredential", 200, { name }, { name });
      return jsonResponse({ ...identity, token });
    }
    if (request.method === "POST" && path === "/_emulate/seed") {
      const body = (await request.json()) as {
        repos?: Array<{
          repo_id: string;
          private?: boolean;
          gated?: boolean | "auto" | "manual";
          tags?: string[];
        }>;
      };
      const seeded = (body.repos ?? []).map((spec) => {
        const repoId = String(spec.repo_id);
        newRepo(repoId, {
          private: Boolean(spec.private),
          gated: spec.gated,
          tags: Array.isArray(spec.tags) ? spec.tags.map(String) : [],
        });
        return repoId;
      });
      appendLedger(request, "seed", 200, { repos: seeded }, { repos_seeded: seeded });
      return jsonResponse({ repos_seeded: seeded });
    }
    if (request.method === "POST" && path === "/_emulate/reset") {
      repos.clear();
      files.clear();
      revisions.clear();
      resetCredentials();
      appendLedger(request, "reset", 200);
      return jsonResponse({ ok: true });
    }
    if (request.method === "POST" && path === "/api/repos/create") {
      const body = (await request.json()) as Record<string, unknown>;
      const name = String(body.name ?? "");
      const organization = body.organization ? String(body.organization) : "bench";
      const repoId = `${organization}/${name}`;
      const ownershipError = requireRepoOwnership(request, "createRepo", repoId);
      if (ownershipError !== undefined) return ownershipError;
      if (repos.has(repoId)) {
        appendLedger(request, "createRepo", 409);
        return errorResponse("Conflict", "repository already exists", 409);
      }
      newRepo(repoId, {
        private: body.visibility === "private" || Boolean(body.private),
      });
      appendLedger(request, "createRepo", 200, body, { repo_id: repoId });
      return jsonResponse({ url: `http://${HOST}:${PORT}/datasets/${repoId}` });
    }
    if (request.method === "GET" && path === "/api/whoami-v2") {
      const identity = authenticatedIdentity(request);
      if (identity === undefined) {
        appendLedger(request, "whoami", 401);
        return errorResponse("InvalidToken", "invalid access token", 401);
      }
      appendLedger(request, "whoami", 200);
      return jsonResponse(identity);
    }
    if (request.method === "GET" && path === "/api/datasets") {
      const authorization = request.headers.get("authorization");
      const identity = authenticatedIdentity(request);
      if (authorization !== null && identity === undefined) {
        appendLedger(request, "listDatasets", 401);
        return errorResponse("InvalidToken", "invalid access token", 401);
      }
      const author = new URL(request.url).searchParams.get("author");
      const result =
        identity === undefined || (author !== null && author !== identity.name)
          ? []
          : [...repos.values()]
              .filter((repo) => repo.repoId.startsWith(`${identity.name}/`))
              .map(datasetInfo);
      appendLedger(request, "listDatasets", 200);
      return jsonResponse(result);
    }
    if (request.method === "DELETE" && path === "/api/repos/delete") {
      const body = (await request.json()) as Record<string, unknown>;
      const name = String(body.name ?? "");
      const organization = body.organization ? String(body.organization) : "bench";
      const repoId = `${organization}/${name}`;
      const ownershipError = requireRepoOwnership(request, "deleteRepo", repoId);
      if (ownershipError !== undefined) return ownershipError;
      if (!repos.delete(repoId)) {
        appendLedger(request, "deleteRepo", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      files.delete(repoId);
      revisions.delete(repoId);
      appendLedger(request, "deleteRepo", 200, body);
      return jsonResponse({ ok: true });
    }
    const settingsMatch = path.match(/^\/api\/datasets\/([^/]+\/[^/]+)\/settings$/);
    if (request.method === "PUT" && settingsMatch !== null) {
      const repoId = decodeURIComponent(settingsMatch[1]);
      const ownershipError = requireRepoOwnership(request, "updateSettings", repoId);
      if (ownershipError !== undefined) return ownershipError;
      const repo = repos.get(repoId);
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
      appendLedger(request, "updateSettings", 200, body);
      return jsonResponse({ ok: true });
    }
    const preuploadMatch = path.match(
      /^\/api\/datasets\/([^/]+\/[^/]+)\/preupload\/([^/]+)$/,
    );
    if (request.method === "POST" && preuploadMatch !== null) {
      const repoId = decodeURIComponent(preuploadMatch[1]);
      const ownershipError = requireRepoOwnership(request, "preupload", repoId);
      if (ownershipError !== undefined) return ownershipError;
      const repo = repos.get(repoId);
      if (repo === undefined) {
        appendLedger(request, "preupload", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      if (!revisionExists(repo, preuploadMatch[2])) {
        appendLedger(request, "preupload", 404);
        return errorResponse("RevisionNotFound", "revision not found", 404);
      }
      const body = (await request.json()) as {
        files?: Array<{ path: string }>;
      };
      const repoFiles = filesAtRevision(repo, preuploadMatch[2]) ?? new Map();
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
      const ownershipError = requireRepoOwnership(request, "commit", repoId);
      if (ownershipError !== undefined) return ownershipError;
      const repo = repos.get(repoId);
      if (repo === undefined) {
        appendLedger(request, "commit", 404);
        return errorResponse("RepoNotFound", "repository not found", 404);
      }
      if (!revisionExists(repo, commitMatch[2])) {
        appendLedger(request, "commit", 404);
        return errorResponse("RevisionNotFound", "revision not found", 404);
      }
      const rawPayload = await request.text();
      const items = rawPayload
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line) as { key: string; value: Record<string, unknown> });
      const revision = decodeURIComponent(commitMatch[2]);
      const header = items.find((item) => item.key === "header");
      const parentCommit =
        header?.value.parentCommit === undefined
          ? undefined
          : String(header.value.parentCommit);
      const addressedHead = revision === "main" ? repo.headOid : revision;
      const commitRequest = {
        repo_id: repoId,
        revision,
        parent_commit: parentCommit,
        files: items
          .filter((item) => item.key === "file")
          .map((item) => String(item.value.path)),
      };
      if (parentCommit !== undefined && parentCommit !== addressedHead) {
        appendLedger(request, "commit", 409, commitRequest, {
          current_head: addressedHead,
        });
        return errorResponse(
          "Conflict",
          "A commit has happened since. Please refresh and try again.",
          409,
        );
      }
      const repoFiles = new Map(filesAtRevision(repo, revision) ?? []);
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
      const revisionFiles = revisions.get(repoId) ?? new Map();
      revisionFiles.set(oid, new Map(repoFiles));
      revisions.set(repoId, revisionFiles);
      repo.updatedAt = new Date().toISOString();
      appendLedger(
        request,
        "commit",
        200,
        commitRequest,
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
      const accessError = requireRepoReadAccess(request, "listRepoTree", repo);
      if (accessError !== undefined) return accessError;
      if (!revisionExists(repo, treeMatch[2])) {
        appendLedger(request, "listRepoTree", 404);
        return errorResponse("RevisionNotFound", "revision not found", 404);
      }
      const prefix = treeMatch[3] ? `${decodeURIComponent(treeMatch[3]).replace(/\/$/, "")}/` : "";
      const result = [...(filesAtRevision(repo, treeMatch[2]) ?? new Map()).entries()]
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
      const accessError = requireRepoReadAccess(request, "resolveFile", repo);
      if (accessError !== undefined) return accessError;
      if (!revisionExists(repo, resolveMatch[2])) {
        appendLedger(request, "resolveFile", 404);
        return errorResponse("RevisionNotFound", "revision not found", 404);
      }
      const filePath = decodeURIComponent(resolveMatch[3]);
      const file = filesAtRevision(repo, resolveMatch[2])?.get(filePath);
      if (file === undefined) {
        appendLedger(request, "resolveFile", 404);
        return errorResponse("EntryNotFound", "file not found", 404);
      }
      const headers = {
        "Content-Length": String(file.content.byteLength),
        "Content-Type": "application/octet-stream",
        ETag: `\"${file.oid}\"`,
        "X-Repo-Commit":
          decodeURIComponent(resolveMatch[2]) === "main"
            ? repo.headOid
            : decodeURIComponent(resolveMatch[2]),
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
      const accessError = requireRepoReadAccess(request, "datasetInfo", repo);
      if (accessError !== undefined) return accessError;
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
