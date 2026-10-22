import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, FolderInput, Pencil, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { mountsQuery, runtimeKeys } from "../../../api/queries/runtime";
import type { Mount } from "../../../api/queries/runtime";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
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
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { hasUnit } from "../../apps/AppRowActions";
import { reportActionError } from "../../apps/useAppActions";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import {
  useSettingsApp,
  useSubsectionIcon,
  useSubsectionTitle,
} from "./settingsApp";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { SETTINGS_ICONS } from "./objectIcons";

const OCTAL_MODE = /^[0-7]{3,4}$/;

function refresh(
  queryClient: ReturnType<typeof useQueryClient>,
  domain: string,
): void {
  void queryClient.invalidateQueries({ queryKey: runtimeKeys.mounts(domain) });
  void queryClient.invalidateQueries({ queryKey: runtimeKeys.pending(domain) });
}

/** The content of a file mount, read when its editor opens (the permission to reveal secrets). */
function useMountContent(domain: string, target: string, enabled: boolean) {
  return useQuery({
    queryKey: ["app", domain, "mount-content", target] as const,
    queryFn: ({ signal }) =>
      request("get", "/api/apps/{domain}/mounts/content", {
        params: { domain },
        query: { target },
        signal,
      }),
    enabled,
    staleTime: 0,
    gcTime: 0,
  });
}

/**
 * A file kept for the application (its text, its permissions), or a folder of the server bound
 * into a container. Editing a file reads its content first: nothing is saved over what could not
 * be read.
 */
function MountDialog({
  domain,
  kind,
  mount,
  open,
  onOpenChange,
}: {
  domain: string;
  kind: "file" | "path";
  mount?: Mount;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const editing = mount !== undefined;
  const formId = useId();
  const [target, setTarget] = useState(mount?.target ?? "");
  const [content, setContent] = useState("");
  const [mode, setMode] = useState(
    mount?.mode.replace(/^0+(?=\d{3})/, "") ?? "600",
  );
  const [source, setSource] = useState(mount?.source ?? "");
  const [readOnly, setReadOnly] = useState(mount?.read_only ?? true);
  const [submitted, setSubmitted] = useState(false);
  const [loaded, setLoaded] = useState(!editing || kind !== "file");

  const current = useMountContent(
    domain,
    mount?.target ?? "",
    editing && kind === "file",
  );
  // The file's text arrives once; what is typed after that is the operator's.
  if (!loaded && current.data !== undefined) {
    setLoaded(true);
    setContent(current.data.content);
  }

  const problems = {
    target: target.trim() === "" ? t("runtime.mounts.targetMissing") : null,
    source:
      kind === "path" && source.trim() === ""
        ? t("runtime.mounts.sourceMissing")
        : null,
    mode:
      kind === "file" && !OCTAL_MODE.test(mode.trim())
        ? t("runtime.mounts.modeRange")
        : null,
  };
  const valid = Object.values(problems).every((problem) => problem === null);
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/mounts", {
        params: { domain },
        body:
          kind === "file"
            ? {
                target: target.trim(),
                kind: "file",
                content,
                mode: Number.parseInt(mode.trim(), 10),
                read_only: false,
              }
            : {
                target: target.trim(),
                kind: "path",
                source: source.trim(),
                read_only: readOnly,
              },
      }),
    onSuccess: () => {
      refresh(queryClient, domain);
      announce(t("runtime.mounts.saved", { target: target.trim() }));
      onOpenChange(false);
    },
  });
  const serverError = (field: string): string | undefined =>
    isApiError(save.error) ? save.error.fields?.[field] : undefined;
  const close = (next: boolean): void => {
    if (!next && save.isPending) return;
    if (!next) save.reset();
    onOpenChange(next);
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (!valid || !loaded || save.isPending) return;
    save.mutate();
  };
  const shown = (field: keyof typeof problems): string | undefined =>
    submitted ? (problems[field] ?? undefined) : undefined;
  const title =
    kind === "path"
      ? t("runtime.mounts.pathDialogTitle")
      : editing
        ? t("runtime.mounts.fileEditTitle", { target: mount.target })
        : t("runtime.mounts.fileDialogTitle");
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      title={title}
      description={
        kind === "path"
          ? t("runtime.mounts.pathDialogDescription")
          : t("runtime.mounts.fileDialogDescription")
      }
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={save.isPending}
            disabled={!loaded}
          >
            {t("common.words.save")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        onSubmit={submit}
        className="flex flex-col gap-5"
      >
        {kind === "path" ? (
          <Field
            label={t("runtime.mounts.sourceLabel")}
            description={t("runtime.mounts.sourceDescription")}
            error={shown("source") ?? serverError("source")}
          >
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              placeholder="/srv/shared/uploads"
              value={source}
              onValueChange={setSource}
            />
          </Field>
        ) : null}
        <Field
          label={t("runtime.mounts.targetLabel")}
          description={
            kind === "path"
              ? t("runtime.mounts.targetPathDescription")
              : t("runtime.mounts.targetFileDescription")
          }
          error={shown("target") ?? serverError("target")}
        >
          <Input
            mono
            disabled={editing}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder={
              kind === "path" ? "/data/uploads" : "config/production.json"
            }
            value={target}
            onValueChange={setTarget}
          />
        </Field>
        {kind === "file" ? (
          <>
            {current.isError ? (
              <ErrorBlock
                compact
                error={current.error}
                title={t("runtime.mounts.contentFailed")}
              />
            ) : !loaded ? (
              <LoadingRegion
                label={t("runtime.mounts.contentLoading")}
                className="flex flex-col gap-2"
              >
                <Skeleton className="h-24 w-full" />
              </LoadingRegion>
            ) : (
              <Field
                label={t("runtime.mounts.contentLabel")}
                error={serverError("content")}
              >
                <Textarea
                  mono
                  rows={10}
                  autoComplete="off"
                  value={content}
                  onChange={(event) => {
                    setContent(event.target.value);
                  }}
                />
              </Field>
            )}
            <Field
              label={t("runtime.mounts.modeLabel")}
              description={t("runtime.mounts.modeDescription")}
              error={shown("mode") ?? serverError("mode")}
              className="sm:w-48"
            >
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                value={mode}
                onValueChange={setMode}
              />
            </Field>
          </>
        ) : (
          <Checkbox
            label={t("runtime.mounts.readOnlyLabel")}
            description={t("runtime.mounts.readOnlyDescription")}
            checked={readOnly}
            onCheckedChange={setReadOnly}
          />
        )}
        {save.isError && (!isApiError(save.error) || !save.error.fields) ? (
          <ErrorBlock
            live
            compact
            error={save.error}
            title={t("runtime.mounts.saveFailed", {
              target: target.trim() || "…",
            })}
          />
        ) : null}
      </form>
    </Dialog>
  );
}

function MountRow({ domain, mount }: { domain: string; mount: Mount }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const isFile = mount.kind === "file";
  const remove = useMutation({
    mutationFn: () =>
      request("delete", "/api/apps/{domain}/mounts", {
        params: { domain },
        query: { target: mount.target },
      }),
    onSuccess: () => {
      refresh(queryClient, domain);
      announce(t("runtime.mounts.removed", { target: mount.target }));
    },
    onError: (error) => {
      reportActionError(
        t("runtime.mounts.removeFailed", { target: mount.target }),
        error,
      );
    },
  });
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex min-w-0 items-baseline gap-2">
          {isFile ? (
            <FileText
              aria-hidden="true"
              className="size-icon-sm shrink-0 self-center text-fg-muted"
            />
          ) : (
            <FolderInput
              aria-hidden="true"
              className="size-icon-sm shrink-0 self-center text-fg-muted"
            />
          )}
          <Mono
            className="text-13 font-medium text-fg"
            truncate
            title={mount.target}
          >
            {mount.target}
          </Mono>
          <Badge>
            {isFile
              ? t("runtime.mounts.kindFile")
              : t("runtime.mounts.kindPath")}
          </Badge>
        </span>
        {mount.source ? (
          <Mono tone="muted" truncate title={mount.source}>
            {mount.source}
          </Mono>
        ) : null}
      </div>
      <span className="flex flex-wrap items-center gap-x-3 text-12 text-fg-muted">
        {isFile ? (
          <Mono tone="muted">
            {t("runtime.mounts.mode", { mode: mount.mode })}
          </Mono>
        ) : null}
        {isFile && mount.size != null ? (
          <span>{formatBytes(mount.size, t.locale)}</span>
        ) : null}
        {!isFile ? (
          <span>
            {mount.read_only
              ? t("runtime.mounts.readOnly")
              : t("runtime.mounts.writable")}
          </span>
        ) : null}
        {!mount.exists ? (
          <span className="text-warn">{t("runtime.mounts.missing")}</span>
        ) : null}
      </span>
      <Menu
        align="end"
        trigger={
          <IconButton
            label={t("runtime.mounts.actionsFor", { target: mount.target })}
            icon={<ICONS.more />}
            size="sm"
            tooltip={false}
          />
        }
      >
        {isFile ? (
          <MenuItem
            icon={<Pencil />}
            onClick={() => {
              setEditing(true);
            }}
          >
            {t("common.words.edit")}
          </MenuItem>
        ) : null}
        {isFile ? <MenuSeparator /> : null}
        <MenuItem
          icon={<Trash2 />}
          destructive
          onClick={() => {
            setRemoving(true);
          }}
        >
          {t("common.words.remove")}
        </MenuItem>
      </Menu>
      {editing ? (
        <MountDialog
          domain={domain}
          kind="file"
          mount={mount}
          open
          onOpenChange={setEditing}
        />
      ) : null}
      <ConfirmDialog
        friction="simple"
        open={removing}
        onOpenChange={setRemoving}
        title={t("runtime.mounts.removeTitle", { target: mount.target })}
        description={
          isFile
            ? t("runtime.mounts.removeFileDescription")
            : t("runtime.mounts.removePathDescription")
        }
        actionLabel={t("common.words.remove")}
        destructive
        onConfirm={async () => {
          await remove.mutateAsync();
        }}
      />
    </li>
  );
}

function MountsCard({ domain }: { domain: string }) {
  const t = useT();
  const mounts = useQuery(mountsQuery(domain));
  const [adding, setAdding] = useState<"file" | "path" | null>(null);
  const data = mounts.data;
  return (
    <Card
      icon={SETTINGS_ICONS.mounts}
      title={t("runtime.mounts.title")}
      description={t("runtime.mounts.description")}
      padding="none"
      actions={
        <>
          {data?.container ? (
            <Button
              size="sm"
              icon={<FolderInput aria-hidden="true" />}
              onClick={() => {
                setAdding("path");
              }}
            >
              {t("runtime.mounts.addPath")}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="primary"
            icon={<FileText aria-hidden="true" />}
            onClick={() => {
              setAdding("file");
            }}
          >
            {t("runtime.mounts.addFile")}
          </Button>
        </>
      }
    >
      {data === undefined ? (
        mounts.isError ? (
          <div className="p-4">
            <ErrorBlock
              compact
              error={mounts.error}
              title={t("runtime.mounts.loadFailed")}
              onRetry={() => void mounts.refetch()}
              retrying={mounts.isRefetching}
            />
          </div>
        ) : (
          <LoadingRegion
            label={t("runtime.mounts.loading")}
            className="flex flex-col gap-2 p-4"
          >
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </LoadingRegion>
        )
      ) : data.mounts.length === 0 ? (
        <div className="p-4">
          <EmptyState
            variant="inline"
            title={t("runtime.mounts.emptyTitle")}
            description={t("runtime.mounts.emptyDescription")}
          />
        </div>
      ) : (
        <>
          <ul
            aria-label={t("runtime.mounts.listLabel", { domain })}
            className="flex flex-col divide-y divide-border"
          >
            {data.mounts.map((mount) => (
              <MountRow key={mount.target} domain={domain} mount={mount} />
            ))}
          </ul>
          <p className="border-t border-border px-4 py-3 text-12 text-pretty text-fg-muted">
            {t("runtime.mounts.note")}
          </p>
        </>
      )}
      {adding !== null ? (
        <MountDialog
          domain={domain}
          kind={adding}
          open
          onOpenChange={(open) => {
            if (!open) setAdding(null);
          }}
        />
      ) : null}
    </Card>
  );
}

/** Mounts: the files kept for the application and, on a container, the folders of the server bound into it. */
export function MountsSettings() {
  const t = useT();
  const app = useSettingsApp();
  useSubsectionTitle("runtime.nav.mounts", app.domain);
  const headerIcon = useSubsectionIcon("runtime.nav.mounts");
  return (
    <Section title={t("runtime.nav.mounts")} {...headerIcon}>
      {hasUnit(app) && app.app_type !== "docker-compose" ? (
        <MountsCard domain={app.domain} />
      ) : (
        <InlineMessage tone="info">{t("runtime.mounts.cannot")}</InlineMessage>
      )}
    </Section>
  );
}
