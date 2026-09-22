import { Types } from "komodo_client";

/**
 * The project config page reads platform data by running the installed Actions and parsing the
 * single machine-readable line they print. The Actions stay the only execution path: the page
 * never runs a local command and never talks to the Windows executor directly.
 */
export const PLATFORM_MARKER = "__PLATFORM_JSON__";

/** Project prefixes are discovered from the installed Actions, not hardcoded here. */
export type PlatformProject = {
  prefix: string;
  listRefs: string;
  versions: string;
  source: string;
  build: string;
  deploy: string;
  rollback: string;
};

const SUFFIXES = {
  listRefs: "-list-refs",
  versions: "-versions",
  source: "-edit-source",
  build: "-build-deploy",
  deploy: "-deploy-version",
  rollback: "-rollback",
} as const;

export function platformProjects(
  actions: Types.ActionListItem[] | undefined,
): PlatformProject[] {
  const names = new Set((actions ?? []).map((action) => action.name));
  const projects: PlatformProject[] = [];
  for (const name of names) {
    if (!name.endsWith(SUFFIXES.versions)) continue;
    const prefix = name.slice(0, -SUFFIXES.versions.length);
    if (!prefix || !names.has(prefix + SUFFIXES.listRefs)) continue;
    projects.push({
      prefix,
      listRefs: prefix + SUFFIXES.listRefs,
      versions: name,
      source: prefix + SUFFIXES.source,
      build: prefix + SUFFIXES.build,
      deploy: prefix + SUFFIXES.deploy,
      rollback: prefix + SUFFIXES.rollback,
    });
  }
  return projects.sort((a, b) => a.prefix.localeCompare(b.prefix));
}

export type PlatformRefs = {
  schema: number;
  job: string;
  title?: string;
  project: string;
  mode?: "local" | "remote" | string;
  remote?: string | null;
  approved?: string[];
  default_ref?: string | null;
  approved_ref_tips?: Record<string, string>;
  approved_ref_matches_release?: Record<string, boolean> | null;
  branches?: string[];
  branch_count?: number;
  tags?: string[];
  tag_count?: number;
  commits?: { sha: string; subject: string }[];
  requested_ref?: string | null;
  requested_ref_approved?: boolean | null;
  commit?: string | null;
  resolution_error?: string | null;
  released_commit?: string | null;
  status: string;
  error?: string;
};

export type PlatformVersions = {
  schema: number;
  job: string;
  title?: string;
  project: string;
  repository?: {
    path?: string;
    exists?: boolean;
    git?: boolean;
    head?: string | null;
    branch?: string | null;
    dirty?: boolean;
    untracked?: number;
  };
  recipe?: {
    digest?: string;
    source_mode?: string;
    approved_refs?: string[];
    default_ref?: string | null;
  };
  candidate?: {
    job?: string;
    action?: string;
    status?: string;
    version?: string | null;
    commit?: string | null;
    deployed?: boolean;
  } | null;
  release?: {
    version?: string;
    commit?: string | null;
    snapshot?: string | null;
    previous_version?: string | null;
    images?: Record<string, string>;
  } | null;
  running?: {
    service: string;
    state: string;
    health?: string | null;
    image_id: string;
    repo_digests?: string[];
    matches_release?: boolean;
    matches_candidate?: boolean;
  }[];
  verdict?: string;
  status: string;
  error?: string;
};

export type PlatformSourceEdit = {
  schema: number;
  job: string;
  title?: string;
  project: string;
  changed?: boolean;
  source?: { mode?: string; refs?: string[]; default_ref?: string };
  previous_source?: { mode?: string; refs?: string[]; default_ref?: string };
  recipe_digest?: string;
  backup?: string;
  unchanged_sections?: string[];
  lock_holder?: string;
  status: string;
  error?: string;
};

export type GroupStatus = "running" | "partial" | "stopped" | "abnormal" | "absent";

export type GroupMember = {
  name: string;
  container_name: string;
  state: string;
  health?: string | null;
  image_id: string;
  image_reference?: string;
  ports?: Record<string, unknown>;
  drift?: boolean | null;
};

export type Group = {
  name: string;
  services: string[];
  managed: boolean;
  status: GroupStatus;
  running: number;
  total: number;
  expected: number;
  healthy: number;
  unhealthy: number;
  starting: number;
  not_created: number;
  missing: string[];
  unexpected: string[];
  unexpected_states?: string[];
  projects: { id: string; title?: string }[];
  containers: GroupMember[];
};

export type GroupSummaryCounts = {
  total: number;
  running: number;
  partial: number;
  stopped: number;
  abnormal: number;
  absent: number;
  unmanaged: number;
  standalone: number;
};

export type PlatformGroups = {
  schema: number;
  job: string;
  groups: Group[];
  registered?: Group[];
  unmanaged?: Group[];
  summary: GroupSummaryCounts;
  lock: { held: boolean; kind?: string; subject?: string | null; pid?: number | null; started_at?: string | null };
  docker_error?: string | null;
  generated_at: string;
  status: string;
  error?: string;
};

/** The locked lifecycle entry installed for one registered group. */
export const stackAction = (group: string) => `${group}-stack`;

export type PlatformRelease = {
  schema: number;
  kind: "release";
  job: string;
  project: string;
  title?: string;
  operation: string;
  status: string;
  error?: string | null;
  version?: string | null;
  source_commit?: string | null;
  images?: Record<string, string> | null;
  current_release?: { version?: string; source?: { commit?: string | null }; images?: Record<string, string> } | null;
};

/** Parse the last marker line of an Action's logs. Never throws on unexpected content. */
export function parsePlatformPayload<T>(
  logs: Types.Log[] | undefined,
): T | undefined {
  let found: T | undefined;
  for (const entry of logs ?? []) {
    const text = [entry.stdout, entry.stderr].filter(Boolean).join("\n");
    for (const line of text.split("\n")) {
      if (!line.startsWith(PLATFORM_MARKER)) continue;
      try {
        const parsed = JSON.parse(line.slice(PLATFORM_MARKER.length).trim());
        if (parsed && typeof parsed === "object" && parsed.schema === 1) {
          found = parsed as T;
        }
      } catch {
        // A malformed line must not break the page; the human log view still shows it.
      }
    }
  }
  return found;
}

/** Human-readable lines of an update, for the page's log panel. */
export function updateText(logs: Types.Log[] | undefined): string {
  return (logs ?? [])
    .flatMap((entry) => [entry.stdout, entry.stderr])
    .filter((text): text is string => !!text && text.trim().length > 0)
    .map((text) => text.replace(/\s+$/, ""))
    .join("\n");
}

export function shortSha(value: string | null | undefined, length = 12): string {
  return value ? value.slice(0, length) : "—";
}

/**
 * The reviewed editor's Action carries its defaults as a JSON *string* (`arguments`), which is
 * what the installer wrote. Reading it wrong silently submits an empty source block, so the
 * page parses it defensively and falls back to blanks instead of inventing values.
 */
export function sourceDefaults(rawArguments: unknown): {
  mode: string;
  remote: string;
  refs: string;
  defaultRef: string;
} {
  let args: Record<string, unknown> = {};
  if (typeof rawArguments === "string") {
    try {
      const parsed = JSON.parse(rawArguments);
      if (parsed && typeof parsed === "object") args = parsed as Record<string, unknown>;
    } catch {
      args = {};
    }
  } else if (rawArguments && typeof rawArguments === "object") {
    args = rawArguments as Record<string, unknown>;
  }
  const text = (value: unknown, fallback: string) =>
    typeof value === "string" ? value : fallback;
  return {
    mode: text(args.mode, "local"),
    remote: text(args.remote, ""),
    refs: text(args.refs, ""),
    defaultRef: text(args.default_ref, ""),
  };
}

/** The last error line of a failed update, for a section to show instead of staying silent. */
export function actionFailureText(logs: Types.Log[] | undefined): string | undefined {
  const text = updateText(logs);
  if (!text) return undefined;
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("Error:") || line.startsWith("error:"));
  return lines.length ? lines[lines.length - 1].replace(/^error:\s*/i, "") : undefined;
}

export function refArgs(ref: string): Record<string, string> {
  return { ref };
}

export const sourceArgs = (
  mode: string,
  remote: string,
  refs: string[],
  defaultRef: string,
): Record<string, string> => ({
  mode,
  remote: mode === "remote" ? remote : "",
  refs: refs.join(","),
  default_ref: defaultRef,
});

export const releaseArgs = (
  operation: "build-deploy" | "deploy" | "rollback" | "reconcile",
  options: { ref?: string; version?: string; job?: string } = {},
): Record<string, string> => {
  const args: Record<string, string> = { operation };
  if (options.ref !== undefined) args.ref = options.ref;
  if (options.version !== undefined) args.version = options.version;
  if (options.job !== undefined) args.job = options.job;
  return args;
};
