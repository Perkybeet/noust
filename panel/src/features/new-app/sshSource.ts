/**
 * A source that is an SSH address, read for what a person needs to do about it when it cannot be
 * cloned: which repository it is, and where its deploy keys are on the host that holds it.
 */

export interface SshRepository {
  /** The host as written, lower-cased: `github.com`. */
  host: string;
  /** The repository's path on it without `.git`: `acme/api`, or `group/sub/api` on GitLab. */
  path: string;
}

/** `git@github.com:acme/api.git` and `ssh://git@github.com/acme/api.git`; anything else is no SSH address. */
export function sshRepository(source: string): SshRepository | null {
  const text = source.trim();
  const scp = /^[\w.-]+@([\w.-]+):(?!\/)(\S+)$/.exec(text);
  const url = /^ssh:\/\/(?:[\w.-]+@)?([\w.-]+)(?::\d+)?\/(\S+)$/i.exec(text);
  const found = scp ?? url;
  if (found === null) return null;
  const [, host, raw] = found;
  if (host === undefined || raw === undefined) return null;
  const path = raw.replace(/^\/+/, "").replace(/\/+$/, "").replace(/\.git$/, "");
  return path === "" ? null : { host: host.toLowerCase(), path };
}

export interface DeployKeysPage {
  host: "GitHub" | "GitLab";
  href: string;
}

/**
 * Where the repository's deploy keys are added, when the host is GitHub or GitLab (their own pages,
 * at the addresses they document); null for any other host, which the person knows better than a
 * guess of ours.
 */
export function deployKeysPage(repository: SshRepository): DeployKeysPage | null {
  if (repository.host === "github.com") {
    return repository.path.split("/").length === 2 ? { host: "GitHub", href: `https://github.com/${repository.path}/settings/keys/new` } : null;
  }
  if (repository.host === "gitlab.com") {
    return { host: "GitLab", href: `https://gitlab.com/${repository.path}/-/settings/repository` };
  }
  return null;
}
