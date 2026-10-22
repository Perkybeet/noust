import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, File as FileIcon, FilePlus, Folder, FolderPlus, Link2, Pencil, Upload } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { ChangeEvent, SyntheticEvent } from "react";

import { api, isApiError, request } from "../../../api/client";
import { filesQuery, runtimeKeys } from "../../../api/queries/runtime";
import type { FileEntry } from "../../../api/queries/runtime";
import { useDocumentTitle } from "../../../app/documentTitle";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { reportActionError } from "../../apps/useAppActions";
import { downloadFile } from "./download";
import { LoadingRegion } from "../../../components/page/LoadingRegion";

/** Joins a folder and a name the way the API reads a path: relative, no leading slash. */
export function joinPath(folder: string, name: string): string {
  return folder === "" ? name : `${folder}/${name}`;
}

/** The folder above a path; "" for the application's own. */
export function parentOf(path: string): string {
  const index = path.lastIndexOf("/");
  return index === -1 ? "" : path.slice(0, index);
}

/** A path cleaned for use as a search parameter: no leading or trailing slash, no empty parts. */
export function cleanFolder(path: unknown): string {
  if (typeof path !== "string") return "";
  return path
    .split("/")
    .filter((part) => part !== "" && part !== "." && part !== "..")
    .join("/")
    .slice(0, 1024);
}

export interface FilesSearch {
  path?: string;
}

export function validateFilesSearch(
  search: Record<string, unknown>,
): FilesSearch {
  const path = cleanFolder(search["path"]);
  return path === "" ? {} : { path };
}

function refresh(
  queryClient: ReturnType<typeof useQueryClient>,
  domain: string,
  folder: string,
): void {
  void queryClient.invalidateQueries({
    queryKey: runtimeKeys.files(domain, folder),
  });
}

// ---------------------------------------------------------------------------------------
// The editor

/** A text file, opened as the application's account reads it and saved the same way. */
function EditorDialog({
  domain,
  path,
  open,
  onOpenChange,
}: {
  domain: string;
  path: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const name = path.slice(path.lastIndexOf("/") + 1);
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState(false);
  const content = useQuery({
    queryKey: ["app", domain, "file-content", path] as const,
    queryFn: ({ signal }) =>
      request("get", "/api/apps/{domain}/files/content", {
        params: { domain },
        query: { path },
        signal,
      }),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  // The file's text arrives once; what is typed after that is the operator's.
  if (!loaded && content.data !== undefined) {
    setLoaded(true);
    setText(content.data.content);
  }
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/files/content", {
        params: { domain },
        body: { path, content: text, create: false },
      }),
    onSuccess: () => {
      refresh(queryClient, domain, parentOf(path));
      toast.success(t("runtime.files.saved", { name }));
      onOpenChange(false);
    },
  });
  const close = (next: boolean): void => {
    if (!next && save.isPending) return;
    if (!next) save.reset();
    onOpenChange(next);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      title={t("runtime.files.editorTitle", { name })}
      description={t("runtime.files.editorDescription")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={!loaded}
            onClick={() => {
              save.mutate();
            }}
          >
            {t("runtime.files.save")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Mono tone="muted">{path}</Mono>
        {content.isError ? (
          <ErrorBlock
            compact
            error={content.error}
            title={t("runtime.files.editorFailed")}
          />
        ) : !loaded ? (
          <LoadingRegion
            label={t("runtime.files.editorLoading")}
            className="flex flex-col gap-2"
          >
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-3/4" />
          </LoadingRegion>
        ) : (
          <Field label={t("runtime.files.editorLabel", { name })}>
            <Textarea
              mono
              rows={18}
              autoComplete="off"
              value={text}
              onChange={(event) => {
                setText(event.target.value);
              }}
            />
          </Field>
        )}
        {save.isError ? (
          <ErrorBlock
            live
            compact
            error={save.error}
            title={t("runtime.files.saveFailed", { name })}
          />
        ) : null}
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// New folder, new file, move

/** A new folder, or a new empty file, in the folder that is open. */
function MakeDialog({
  domain,
  folder,
  kind,
  open,
  onOpenChange,
}: {
  domain: string;
  folder: string;
  kind: "folder" | "file";
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [name, setName] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const clean = name.trim();
  const problem =
    clean === ""
      ? t("runtime.files.nameMissing")
      : clean.includes("/")
        ? t("runtime.files.nameSlash")
        : null;
  const make = useMutation({
    mutationFn: () =>
      kind === "folder"
        ? request("post", "/api/apps/{domain}/files/directory", {
            params: { domain },
            body: { path: joinPath(folder, clean) },
          })
        : request("put", "/api/apps/{domain}/files/content", {
            params: { domain },
            body: { path: joinPath(folder, clean), content: "", create: true },
          }),
    onSuccess: () => {
      refresh(queryClient, domain, folder);
      toast.success(
        kind === "folder"
          ? t("runtime.files.folderCreated", { name: clean })
          : t("runtime.files.fileCreated", { name: clean }),
      );
      onOpenChange(false);
    },
  });
  const close = (next: boolean): void => {
    if (!next && make.isPending) return;
    if (!next) make.reset();
    onOpenChange(next);
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (problem !== null || make.isPending) return;
    make.mutate();
  };
  const where = folder === "" ? "/" : `/${folder}`;
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="sm"
      title={
        kind === "folder"
          ? t("runtime.files.folderTitle")
          : t("runtime.files.fileTitle")
      }
      description={t("runtime.files.folderDescription", { path: where })}
      footer={
        <>
          <Button disabled={make.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={make.isPending}
          >
            {t("runtime.files.create")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        onSubmit={submit}
        className="flex flex-col gap-4"
      >
        <Field
          label={t("runtime.files.nameLabel")}
          error={
            (submitted ? problem : null) ??
            (isApiError(make.error) ? make.error.fields?.["path"] : undefined)
          }
        >
          <Input
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            value={name}
            onValueChange={setName}
          />
        </Field>
        {make.isError && (!isApiError(make.error) || !make.error.fields) ? (
          <ErrorBlock
            live
            compact
            error={make.error}
            title={t("runtime.files.folderFailed", { name: clean || "…" })}
          />
        ) : null}
      </form>
    </Dialog>
  );
}

function MoveDialog({
  domain,
  entry,
  folder,
  open,
  onOpenChange,
}: {
  domain: string;
  entry: FileEntry;
  folder: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const source = joinPath(folder, entry.name);
  const [destination, setDestination] = useState(source);
  const [submitted, setSubmitted] = useState(false);
  const clean = cleanFolder(destination);
  const move = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/files/move", {
        params: { domain },
        body: { source, destination: clean, overwrite: false },
      }),
    onSuccess: () => {
      refresh(queryClient, domain, folder);
      refresh(queryClient, domain, parentOf(clean));
      toast.success(t("runtime.files.moved", { name: entry.name }));
      onOpenChange(false);
    },
  });
  const close = (next: boolean): void => {
    if (!next && move.isPending) return;
    if (!next) move.reset();
    onOpenChange(next);
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (clean === "" || clean === source || move.isPending) return;
    move.mutate();
  };
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="sm"
      title={t("runtime.files.moveTitle", { name: entry.name })}
      description={t("runtime.files.moveDescription")}
      footer={
        <>
          <Button disabled={move.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={move.isPending}
          >
            {t("runtime.files.move")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        onSubmit={submit}
        className="flex flex-col gap-4"
      >
        <Field
          label={t("runtime.files.moveLabel")}
          error={
            (submitted && clean === ""
              ? t("runtime.files.moveMissing")
              : null) ??
            (isApiError(move.error)
              ? move.error.fields?.["destination"]
              : undefined)
          }
        >
          <Input
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            value={destination}
            onValueChange={setDestination}
          />
        </Field>
        {move.isError && (!isApiError(move.error) || !move.error.fields) ? (
          <ErrorBlock
            live
            compact
            error={move.error}
            title={t("runtime.files.moveFailed", { name: entry.name })}
          />
        ) : null}
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// The listing

function EntryActions({
  domain,
  folder,
  entry,
  onOpen,
  onEdit,
}: {
  domain: string;
  folder: string;
  entry: FileEntry;
  onOpen: () => void;
  onEdit: () => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [moving, setMoving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const path = joinPath(folder, entry.name);
  const isDir = entry.kind === "dir";
  const isFile = entry.kind === "file";
  const remove = useMutation({
    mutationFn: () =>
      request("delete", "/api/apps/{domain}/files", {
        params: { domain },
        query: { path, recursive: isDir },
      }),
    onSuccess: () => {
      refresh(queryClient, domain, folder);
      toast.success(t("runtime.files.removed", { name: entry.name }));
    },
    onError: (error) => {
      reportActionError(
        t("runtime.files.removeFailed", { name: entry.name }),
        error,
      );
    },
  });
  const confirmation = {
    open: removing,
    onOpenChange: setRemoving,
    title: t("runtime.files.removeTitle", { name: entry.name }),
    description: isDir
      ? t("runtime.files.removeFolderDescription")
      : entry.kind === "link"
        ? t("runtime.files.removeLinkDescription")
        : t("runtime.files.removeFileDescription"),
    actionLabel: t("runtime.files.remove"),
    destructive: true,
    onConfirm: async () => {
      await remove.mutateAsync();
    },
  };
  return (
    <>
      <Menu
        align="end"
        trigger={
          <IconButton
            label={t("runtime.files.actionsFor", { name: entry.name })}
            icon={<ICONS.more />}
            size="sm"
            tooltip={false}
          />
        }
      >
        {isDir ? (
          <MenuItem icon={<Folder />} onClick={onOpen}>
            {t("runtime.files.open")}
          </MenuItem>
        ) : null}
        {isFile && !entry.sensitive ? (
          <MenuItem icon={<Pencil />} onClick={onEdit}>
            {t("runtime.files.edit")}
          </MenuItem>
        ) : null}
        {isFile && !entry.sensitive ? (
          <MenuItem
            icon={<Download />}
            onClick={() => {
              downloadFile(domain, path, entry.name).catch((error: unknown) => {
                reportActionError(t("runtime.files.editorFailed"), error);
              });
            }}
          >
            {t("runtime.files.download")}
          </MenuItem>
        ) : null}
        {entry.sensitive ? null : (
          <MenuItem
            icon={<FilePlus />}
            onClick={() => {
              setMoving(true);
            }}
          >
            {t("runtime.files.rename")}
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem
          icon={<ICONS.delete />}
          destructive
          onClick={() => {
            setRemoving(true);
          }}
        >
          {t("runtime.files.remove")}
        </MenuItem>
      </Menu>
      {moving ? (
        <MoveDialog
          domain={domain}
          entry={entry}
          folder={folder}
          open
          onOpenChange={setMoving}
        />
      ) : null}
      {isDir ? (
        <ConfirmDialog confirmText={entry.name} {...confirmation} />
      ) : (
        <ConfirmDialog friction="simple" {...confirmation} />
      )}
    </>
  );
}

function EntryRow({
  domain,
  folder,
  entry,
  onFolder,
}: {
  domain: string;
  folder: string;
  entry: FileEntry;
  onFolder: (path: string) => void;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const path = joinPath(folder, entry.name);
  const isDir = entry.kind === "dir";
  const isFile = entry.kind === "file";
  const Icon = isDir ? Folder : entry.kind === "link" ? Link2 : FileIcon;
  const name = (
    <span className="flex min-w-0 items-center gap-2">
      <Icon
        aria-hidden="true"
        className="size-icon-sm shrink-0 text-fg-muted"
      />
      <Mono truncate className="text-13 text-fg" title={entry.name}>
        {entry.name}
      </Mono>
    </span>
  );
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5">
      <div className="min-w-0 flex-1">
        {isDir ? (
          <button
            type="button"
            className="min-w-0 max-w-full cursor-pointer rounded-control text-left hover:underline"
            aria-label={t("runtime.files.openFolder", { name: entry.name })}
            onClick={() => {
              onFolder(path);
            }}
          >
            {name}
          </button>
        ) : isFile && !entry.sensitive ? (
          <button
            type="button"
            className="min-w-0 max-w-full cursor-pointer rounded-control text-left hover:underline"
            aria-label={t("runtime.files.editFile", { name: entry.name })}
            onClick={() => {
              setEditing(true);
            }}
          >
            {name}
          </button>
        ) : (
          name
        )}
        {entry.kind === "link" && entry.target ? (
          <p className="mt-0.5 truncate text-12 text-fg-muted">
            {t("runtime.files.kindLink", { target: entry.target })}
          </p>
        ) : null}
        {entry.sensitive ? (
          <p className="mt-0.5 flex items-center gap-2 text-12 text-fg-muted">
            <Badge>{t("runtime.files.sensitive")}</Badge>
            {t("runtime.files.sensitiveNote")}
          </p>
        ) : null}
      </div>
      <span className="w-20 text-right text-12 text-fg-muted tabular-nums">
        {isDir ? "" : formatBytes(entry.size, t.locale)}
      </span>
      <span className="w-24 text-12 text-fg-muted">
        <RelativeTime value={entry.modified} />
      </span>
      <Mono tone="muted" className="w-12 text-12">
        {entry.mode}
      </Mono>
      <Mono tone="muted" truncate className="w-24 text-12" title={entry.owner}>
        {entry.owner}
      </Mono>
      <EntryActions
        domain={domain}
        folder={folder}
        entry={entry}
        onOpen={() => {
          onFolder(path);
        }}
        onEdit={() => {
          setEditing(true);
        }}
      />
      {editing ? (
        <EditorDialog
          domain={domain}
          path={path}
          open
          onOpenChange={setEditing}
        />
      ) : null}
    </li>
  );
}

/** The folders above the one open, each a link to it. */
function Breadcrumb({
  domain,
  folder,
  onFolder,
}: {
  domain: string;
  folder: string;
  onFolder: (path: string) => void;
}) {
  const t = useT();
  const parts = folder === "" ? [] : folder.split("/");
  return (
    <nav
      aria-label={t("runtime.files.breadcrumbLabel")}
      className="flex min-w-0 flex-wrap items-center gap-1 text-13"
    >
      <TextLink
        to="/apps/$domain/files"
        params={{ domain }}
        size="ui"
        onClick={(event) => {
          event.preventDefault();
          onFolder("");
        }}
      >
        {t("runtime.files.root", { domain })}
      </TextLink>
      {parts.map((part, index) => {
        const path = parts.slice(0, index + 1).join("/");
        const last = index === parts.length - 1;
        return (
          <span key={path} className="flex min-w-0 items-center gap-1">
            <span aria-hidden="true" className="text-fg-faint">
              /
            </span>
            {last ? (
              <Mono aria-current="page" className="text-13 text-fg">
                {part}
              </Mono>
            ) : (
              <TextLink
                to="/apps/$domain/files"
                params={{ domain }}
                search={{ path }}
                size="ui"
                onClick={(event) => {
                  event.preventDefault();
                  onFolder(path);
                }}
              >
                {part}
              </TextLink>
            )}
          </span>
        );
      })}
    </nav>
  );
}

function UploadButton({ domain, folder }: { domain: string; folder: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState<{
    file: File;
    replace: boolean;
  } | null>(null);
  const upload = useMutation({
    mutationFn: ({ file, replace }: { file: File; replace: boolean }) =>
      api(
        "PUT",
        `/api/apps/${encodeURIComponent(domain)}/files/upload?path=${encodeURIComponent(joinPath(folder, file.name))}&overwrite=${String(replace)}`,
        file,
      ),
    onSuccess: (_result, { file }) => {
      refresh(queryClient, domain, folder);
      setPending(null);
      toast.success(t("runtime.files.uploaded", { name: file.name }));
    },
    onError: (error, { file }) => {
      // An existing file is asked about, not reported as a failure.
      if (
        isApiError(error) &&
        error.fields?.["path"] !== undefined &&
        error.detail.includes("exists already")
      ) {
        setPending({ file, replace: true });
        return;
      }
      reportActionError(
        t("runtime.files.uploadFailed", { name: file.name }),
        error,
      );
    },
  });
  const chosen = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) upload.mutate({ file, replace: false });
  };
  return (
    <>
      <input
        ref={input}
        type="file"
        className="sr-only"
        tabIndex={-1}
        aria-label={t("runtime.files.uploadLabel", {
          path: folder === "" ? "/" : `/${folder}`,
        })}
        onChange={chosen}
      />
      <Button
        icon={<Upload aria-hidden="true" />}
        loading={upload.isPending}
        onClick={() => {
          input.current?.click();
        }}
      >
        {t("runtime.files.upload")}
      </Button>
      <ConfirmDialog
        friction="simple"
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
        title={t("runtime.files.uploadReplace", {
          name: pending?.file.name ?? "",
        })}
        description={t("runtime.files.removeFileDescription")}
        actionLabel={t("runtime.files.replace")}
        destructive
        onConfirm={async () => {
          if (pending) await upload.mutateAsync(pending);
        }}
      />
    </>
  );
}

export interface FilesPageProps {
  domain: string;
  folder: string;
  onFolder: (path: string) => void;
}

/**
 * An application's files, as the account it runs as sees them: a folder at a time, with the
 * folder in the URL. Open a text file to edit it, upload, download, make a folder or a file,
 * rename or move, delete. Links are shown and never followed; environment files are the
 * Environment's and are not opened here. Off the tab strip, linked from "More actions".
 */
export function FilesPage({ domain, folder, onFolder }: FilesPageProps) {
  const t = useT();
  useDocumentTitle(t("runtime.files.documentTitle", { domain }));
  const listing = useQuery(filesQuery(domain, folder));
  const [making, setMaking] = useState<"folder" | "file" | null>(null);
  const entries = listing.data?.entries ?? [];
  return (
    <Section
      title={t("runtime.files.title")}
      icon={Folder}
      description={t("runtime.files.description", { domain })}
      actions={
        <>
          <Button
            icon={<FolderPlus aria-hidden="true" />}
            onClick={() => {
              setMaking("folder");
            }}
          >
            {t("runtime.files.newFolder")}
          </Button>
          <Button
            icon={<FilePlus aria-hidden="true" />}
            onClick={() => {
              setMaking("file");
            }}
          >
            {t("runtime.files.newFile")}
          </Button>
          <UploadButton domain={domain} folder={folder} />
        </>
      }
    >
      <Breadcrumb domain={domain} folder={folder} onFolder={onFolder} />
      <Card padding="none">
        {listing.data === undefined ? (
          listing.isError ? (
            <div className="p-4">
              <ErrorBlock
                compact
                error={listing.error}
                title={t("runtime.files.loadFailed")}
                onRetry={() => void listing.refetch()}
                retrying={listing.isRefetching}
              />
            </div>
          ) : (
            <LoadingRegion
              label={t("runtime.files.loading")}
              className="flex flex-col gap-2 p-4"
            >
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </LoadingRegion>
          )
        ) : entries.length === 0 ? (
          <div className="p-4">
            <EmptyState
              variant="inline"
              title={t("runtime.files.emptyTitle")}
              description={t("runtime.files.emptyDescription")}
            />
          </div>
        ) : (
          <ul
            aria-label={t("runtime.files.listLabel", {
              path: folder === "" ? "/" : `/${folder}`,
            })}
            className="flex flex-col divide-y divide-border"
          >
            {entries.map((entry) => (
              <EntryRow
                key={entry.name}
                domain={domain}
                folder={folder}
                entry={entry}
                onFolder={onFolder}
              />
            ))}
          </ul>
        )}
      </Card>
      {listing.data?.truncated ? (
        <p className="text-12 text-fg-muted">
          {t("runtime.files.truncated", { count: entries.length })}
        </p>
      ) : null}
      {making !== null ? (
        <MakeDialog
          domain={domain}
          folder={folder}
          kind={making}
          open
          onOpenChange={(open) => {
            if (!open) setMaking(null);
          }}
        />
      ) : null}
    </Section>
  );
}
