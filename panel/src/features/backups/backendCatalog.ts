/**
 * Backend metadata the API's `/backends` catalogue does not carry: a human label, a short
 * description, and which backends authenticate through a token pasted from `rclone
 * authorize` instead of a form field. Mirrors `BACKEND_FIELDS` in
 * `wasm.managers.backup_destinations`; a backend not listed here (the server added one this
 * build does not know about yet) still renders, under its own raw name.
 */

interface BackendMeta {
  label: string;
  description: string;
}

const BACKEND_META: Readonly<Record<string, BackendMeta>> = {
  sftp: { label: "SFTP server", description: "Any server reachable over SSH." },
  smb: { label: "SMB / CIFS share", description: "A Windows share or a NAS." },
  webdav: { label: "WebDAV", description: "Nextcloud, ownCloud, SharePoint and other WebDAV servers." },
  s3: { label: "S3-compatible storage", description: "AWS S3, Cloudflare R2, Backblaze, Wasabi, MinIO, Hetzner, Scaleway and others, by provider." },
  b2: { label: "Backblaze B2 (native)", description: "Backblaze's own API, rather than its S3-compatible one." },
  drive: { label: "Google Drive", description: "Signed in once from your own computer." },
  onedrive: { label: "Microsoft OneDrive", description: "Signed in once from your own computer." },
  dropbox: { label: "Dropbox", description: "Signed in once from your own computer." },
  pcloud: { label: "pCloud", description: "Signed in once from your own computer." },
  local: { label: "Local path", description: "Another directory or mounted filesystem on this machine." },
};

/**
 * Backends that authenticate through a token pasted from `rclone authorize "<backend>"`, run
 * by the operator on their own machine - never on the server, which never sees the sign-in.
 */
export const OAUTH_BACKENDS: ReadonlySet<string> = new Set(["drive", "onedrive", "dropbox", "pcloud"]);

export function backendLabel(backend: string): string {
  return BACKEND_META[backend]?.label ?? backend;
}

export function backendDescription(backend: string): string | undefined {
  return BACKEND_META[backend]?.description;
}
