import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil } from "lucide-react";
import { useId, useRef, useState } from "react";

import { authKeys, sessionQuery } from "../../api/queries/auth";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { BackupCodesDialog } from "./BackupCodes";
import { suggestedName } from "./passkeyName";
import { passkeyKeys, passkeysQuery, registerPasskey, removePasskey, renamePasskey } from "./passkeys";
import type { Passkey, PasskeyAvailability } from "./passkeys";
import { CeremonyError, browserLimit } from "./webauthn";

/** Why passkeys cannot work from this page, as the server and then the browser know it. */
function Unavailable({ t, availability }: { t: T; availability: PasskeyAvailability | undefined }) {
  const limit = browserLimit();
  if (availability !== undefined && !availability.supported) {
    return (
      <Notice tone="warning" title={t("auth.passkeys.unavailableTitle")}>
        <div className="flex flex-col gap-2">
          {/* The fix in the operator's language when the reason is a known one; the server's own otherwise. */}
          {availability.reason === "ip_address" ? (
            <p className="max-w-measure text-pretty">{t("auth.passkeys.ipAddress")}</p>
          ) : availability.reason === "insecure_context" ? (
            <p className="max-w-measure text-pretty">{t("auth.passkeys.insecureContext")}</p>
          ) : availability.hint ? (
            <p className="max-w-measure text-pretty">{availability.hint}</p>
          ) : null}
          {availability.detail ? (
            <SystemOutput label={t("auth.passkeys.serverSaid")} maxHeight="max-h-24">
              {availability.detail}
            </SystemOutput>
          ) : null}
        </div>
      </Notice>
    );
  }
  if (limit !== null) {
    return (
      <Notice tone="warning" title={t("auth.passkeys.unavailableTitle")}>
        {limit === "insecure_context"
          ? t("auth.passkeys.insecureContext")
          : limit === "ip_address"
            ? t("auth.passkeys.ipAddress")
            : t("auth.passkeys.noWebAuthn")}
      </Notice>
    );
  }
  return null;
}

function AddDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const [name, setName] = useState(() => suggestedName(t));
  const [codes, setCodes] = useState<readonly string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [nudge, setNudge] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const formId = useId();
  const add = useMutation({
    mutationFn: () => registerPasskey(name.trim()),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: passkeyKeys.list });
      void queryClient.invalidateQueries({ queryKey: authKeys.session });
      void queryClient.invalidateQueries({ queryKey: authKeys.twoFactor });
      if (result.backup_codes && result.backup_codes.length > 0) {
        setCodes(result.backup_codes);
        return;
      }
      toast.success(t("auth.passkeys.addedToast", { name: result.passkey.name }));
      onClose();
    },
  });
  const failure = add.isError && !(add.error instanceof CeremonyError && add.error.cancelled) ? add.error : null;

  if (codes !== null) {
    return (
      <BackupCodesDialog
        open
        codes={codes}
        hostname={session?.hostname ?? ""}
        description={t("auth.passkeys.codesDescription")}
        saved={saved}
        nudge={nudge}
        onSavedChange={(next) => {
          setSaved(next);
          if (next) setNudge(false);
        }}
        onOpenChange={(next) => {
          if (next) return;
          if (!saved) {
            setNudge(true);
            return;
          }
          onClose();
        }}
      />
    );
  }

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !add.isPending) onClose();
      }}
      size="sm"
      initialFocus={inputRef}
      title={t("auth.passkeys.addTitle")}
      description={t("auth.passkeys.addDescription")}
      footer={
        <>
          <Button disabled={add.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" icon={<KeyRound aria-hidden="true" />} loading={add.isPending} disabled={name.trim() === ""}>
            {t("auth.passkeys.create")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim() !== "" && !add.isPending) add.mutate();
        }}
      >
        <Field label={t("auth.passkeys.nameLabel")} description={t("auth.passkeys.nameHint")}>
          <Input
            ref={inputRef}
            maxLength={64}
            value={name}
            onValueChange={(value: string) => {
              setName(value);
            }}
          />
        </Field>
        {failure !== null ? <ErrorBlock live compact error={failure} title={t("auth.passkeys.addFailed")} /> : null}
      </form>
    </Dialog>
  );
}

function RenameDialog({ passkey, onClose }: { passkey: Passkey; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [name, setName] = useState(passkey.name);
  const formId = useId();
  const rename = useMutation({
    mutationFn: () => renamePasskey(passkey.id, name.trim()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: passkeyKeys.list });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !rename.isPending) onClose();
      }}
      size="sm"
      title={t("auth.passkeys.renameTitle")}
      footer={
        <>
          <Button disabled={rename.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={rename.isPending} disabled={name.trim() === "" || name.trim() === passkey.name}>
            {t("auth.passkeys.renameSubmit")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (!rename.isPending) rename.mutate();
        }}
      >
        <Field label={t("auth.passkeys.nameLabel")}>
          <Input
            maxLength={64}
            value={name}
            onValueChange={(value: string) => {
              setName(value);
            }}
          />
        </Field>
        {rename.isError ? <ErrorBlock live compact error={rename.error} title={t("auth.passkeys.renameFailed")} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * Passkeys: sign in with the device's own lock (a fingerprint, a face, a PIN) instead of a
 * password and a code, and confirm it's you the same way. Each is named, can be renamed and
 * removed; the list says whether one may be synced to other devices. Where the browser or the
 * address rules passkeys out, the section says why and how to fix it instead of vanishing.
 */
export function PasskeysSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(passkeysQuery());
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<Passkey | null>(null);
  const [removing, setRemoving] = useState<Passkey | null>(null);
  const available = query.data?.availability.supported === true && browserLimit() === null;
  const passkeys = query.data?.passkeys ?? [];

  const columns: Column<Passkey>[] = [
    {
      id: "name",
      header: t("auth.passkeys.columnName"),
      card: "title",
      cell: (passkey) => (
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="truncate text-13 text-fg">{passkey.name}</span>
          <Badge>{passkey.synced ? t("auth.passkeys.synced") : t("auth.passkeys.deviceBound")}</Badge>
        </span>
      ),
    },
    { id: "created", header: t("auth.passkeys.columnCreated"), hideBelow: "md", cell: (passkey) => <RelativeTime value={passkey.created_at} /> },
    {
      id: "used",
      header: t("auth.passkeys.columnLastUsed"),
      cell: (passkey) => <RelativeTime value={passkey.last_used_at} fallback={t("auth.passkeys.neverUsed")} />,
    },
    { id: "rp", header: t("auth.passkeys.columnBoundTo"), hideBelow: "lg", card: "hidden", cell: (passkey) => <Mono tone="muted">{passkey.rp_id}</Mono> },
  ];

  return (
    <Section
      title={t("auth.passkeys.title")}
      description={t("auth.passkeys.description")}
      {...(available && passkeys.length > 0
        ? {
            actions: (
              <Button size="sm" icon={<KeyRound aria-hidden="true" />} onClick={() => setAdding(true)}>
                {t("auth.passkeys.add")}
              </Button>
            ),
          }
        : {})}
    >
      <Unavailable t={t} availability={query.data?.availability} />
      {available && passkeys.length === 1 ? <Notice title={t("auth.passkeys.addSecondTitle")}>{t("auth.passkeys.addSecond")}</Notice> : null}
      {query.isError ? (
        <ErrorBlock error={query.error} title={t("auth.passkeys.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : query.data !== undefined && !available && passkeys.length === 0 ? null : (
        <DataTable
          caption={t("auth.passkeys.caption")}
          columns={columns}
          rows={passkeys}
          getRowId={(passkey) => String(passkey.id)}
          density="compact"
          mobile="cards"
          loading={query.isPending}
          skeletonRows={1}
          empty={
            <EmptyState
              variant="inline"
              title={t("auth.passkeys.none")}
              {...(available
                ? {
                    action: (
                      <Button size="sm" icon={<KeyRound aria-hidden="true" />} onClick={() => setAdding(true)}>
                        {t("auth.passkeys.add")}
                      </Button>
                    ),
                  }
                : {})}
            />
          }
          rowActions={(passkey) => (
            <Menu align="end" trigger={<IconButton label={t("auth.passkeys.actionsFor", { name: passkey.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
              <MenuItem icon={<Pencil />} onClick={() => setRenaming(passkey)}>
                {t("auth.passkeys.rename")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem icon={<ICONS.delete />} destructive onClick={() => setRemoving(passkey)}>
                {t("auth.passkeys.remove")}
              </MenuItem>
            </Menu>
          )}
        />
      )}
      {adding ? <AddDialog onClose={() => setAdding(false)} /> : null}
      {renaming !== null ? <RenameDialog passkey={renaming} onClose={() => setRenaming(null)} /> : null}
      {removing !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setRemoving(null);
          }}
          title={t("auth.passkeys.removeTitle", { name: removing.name })}
          description={passkeys.length === 1 ? t("auth.passkeys.removeLastDescription") : t("auth.passkeys.removeDescription")}
          actionLabel={t("auth.passkeys.removeAction")}
          onConfirm={async () => {
            await removePasskey(removing.id);
            void queryClient.invalidateQueries({ queryKey: passkeyKeys.list });
            void queryClient.invalidateQueries({ queryKey: authKeys.session });
          }}
        />
      ) : null}
    </Section>
  );
}
