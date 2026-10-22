import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, PlugZap } from "lucide-react";
import { useId, useMemo, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { secretProviderKindsQuery, secretProvidersQuery, variableKeys } from "../../api/queries/variables";
import type { SecretProvider, SecretProviderKind } from "../../api/queries/variables";
import { announce } from "../../app/Announcer";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { StatusPill } from "../../components/ui/StatusPill";
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
import { Select } from "../../components/ui/Select";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { SettingsPrimaryAction } from "../settings/SettingsShell";
import { useOrganization } from "../teams/useOrganization";
import { useCan } from "./useCan";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/** What a name may be, as the server reads it: lowercase letters, digits and dashes. */
const NAME = /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/;

/** How a reference to a secret in a manager is written; a syntax, never translated. */
const syntaxOf = (name: string): string => `{{secret:${name}/path}}`;

function kindName(t: T, kind: string): string {
  return kind === "vault" || kind === "doppler" || kind === "infisical" ? t(`variables.providers.kindNames.${kind}`) : kind;
}

interface ProviderTarget {
  /** The provider being changed; null when adding. */
  provider: SecretProvider | null;
}

const EVERY_TEAM = "";

interface FormProps {
  formId: string;
  target: ProviderTarget;
  kinds: readonly SecretProviderKind[];
  onSubmit: (values: Values) => Promise<void>;
  onPending: (pending: boolean) => void;
}

interface Values {
  name: string;
  kind: string;
  address: string;
  settings: Record<string, string>;
  token: string;
  team: string;
}

function ProviderForm({ formId, target, kinds, onSubmit, onPending }: FormProps) {
  const t = useT();
  const org = useOrganization();
  const editing = target.provider !== null;
  const first = kinds[0]?.kind ?? "vault";
  const [name, setName] = useState(target.provider?.name ?? "");
  const [kind, setKind] = useState(target.provider?.kind ?? first);
  const [address, setAddress] = useState(target.provider?.address ?? "");
  const [settings, setSettings] = useState<Record<string, string>>({ ...(target.provider?.settings ?? {}) });
  const [token, setToken] = useState("");
  const [team, setTeam] = useState(target.provider?.team ?? EVERY_TEAM);
  const [submitted, setSubmitted] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  const spec = kinds.find((candidate) => candidate.kind === kind);
  const needsAddress = spec !== undefined && (spec.default_address === null || spec.default_address === undefined);
  const required = Object.keys(spec?.required ?? {});
  const optional = Object.keys(spec?.optional ?? {});

  const trimmedName = name.trim();
  const nameError = !submitted ? null : trimmedName === "" ? t("variables.providers.dialog.nameRequired") : NAME.test(trimmedName) ? null : t("variables.providers.dialog.nameInvalid");
  const addressError = submitted && needsAddress && address.trim() === "" ? t("variables.providers.dialog.addressRequiredError") : null;
  const tokenError = submitted && !editing && token === "" ? t("variables.providers.dialog.tokenRequired") : null;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (trimmedName === "" || !NAME.test(trimmedName) || addressError !== null || (!editing && token === "")) return;
    setFailure(null);
    onPending(true);
    const kept = Object.fromEntries(Object.entries(settings).filter(([key, value]) => [...required, ...optional].includes(key) && value.trim() !== "").map(([key, value]) => [key, value.trim()]));
    onSubmit({ name: trimmedName, kind, address: address.trim(), settings: kept, token, team }).then(
      () => onPending(false),
      (error: unknown) => {
        onPending(false);
        setFailure(error);
      },
    );
  };

  const teams = org.teams.length > 1 ? org.teams : [];

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("variables.providers.dialog.nameLabel")} description={editing ? undefined : t("variables.providers.dialog.nameDescription")} error={nameError}>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="vault" value={name} disabled={editing} onValueChange={setName} />
        </Field>
        <Field label={t("variables.providers.dialog.kindLabel")} nativeLabel={false}>
          <Select
            aria-label={t("variables.providers.dialog.kindLabel")}
            value={kind}
            disabled={editing}
            options={kinds.map((option) => ({ value: option.kind, label: kindName(t, option.kind) }))}
            onValueChange={setKind}
          />
        </Field>
      </div>
      <Field
        label={t("variables.providers.dialog.addressLabel")}
        optional={!needsAddress}
        description={needsAddress ? t("variables.providers.dialog.addressRequired") : t("variables.providers.dialog.addressOptional")}
        error={addressError}
      >
        <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder={spec?.default_address ?? "https://vault.example.com:8200"} value={address} onValueChange={setAddress} />
      </Field>
      {required.length + optional.length > 0 ? (
        <fieldset className="flex min-w-0 flex-col gap-4">
          <legend className="mb-2 text-13 font-medium text-fg">{t("variables.providers.dialog.settingsLegend", { kind: kindName(t, kind) })}</legend>
          {[...required.map((key) => ({ key, optional: false })), ...optional.map((key) => ({ key, optional: true }))].map(({ key, optional: isOptional }) => (
            <Field
              key={key}
              label={key}
              optional={isOptional}
              description={(isOptional ? spec?.optional[key] : spec?.required[key]) ?? undefined}
              error={submitted && !isOptional && (settings[key] ?? "").trim() === "" ? t("variables.providers.dialog.settingRequired", { name: key }) : null}
            >
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={settings[key] ?? ""}
                onValueChange={(next: string) => setSettings((current) => ({ ...current, [key]: next }))}
              />
            </Field>
          ))}
        </fieldset>
      ) : null}
      <Field label={t("variables.providers.dialog.tokenLabel")} description={editing ? t("variables.providers.dialog.tokenKeep") : t("variables.providers.dialog.tokenDescription")} error={tokenError} optional={editing}>
        <Input type="password" autoComplete="new-password" autoCapitalize="off" spellCheck={false} mono value={token} onValueChange={setToken} />
      </Field>
      {teams.length > 0 ? (
        <Field label={t("variables.providers.dialog.teamLabel")} description={t("variables.providers.dialog.teamDescription")} nativeLabel={false}>
          <Select
            aria-label={t("variables.providers.dialog.teamLabel")}
            value={team}
            options={[{ value: EVERY_TEAM, label: t("variables.providers.dialog.teamEvery") }, ...teams.map((option) => ({ value: option.slug, label: option.name }))]}
            onValueChange={setTeam}
          />
        </Field>
      ) : null}
      {failure !== null ? <ErrorBlock live compact error={failure} title={t("variables.providers.saveFailed", { name: trimmedName })} /> : null}
    </form>
  );
}

/** The dialog around the form: mounted fresh for every opening, so it starts from the target's values. */
function ProviderDialog({ target, kinds, onClose, onSubmit }: { target: ProviderTarget | null; kinds: readonly SecretProviderKind[]; onClose: () => void; onSubmit: (values: Values) => Promise<void> }) {
  const t = useT();
  const formId = useId();
  const [pending, setPending] = useState(false);
  const [shown, setShown] = useState<{ target: ProviderTarget; generation: number } | null>(null);
  if (target !== null && target !== shown?.target) setShown({ target, generation: (shown?.generation ?? 0) + 1 });
  const current = target ?? shown?.target ?? null;
  const editing = current?.provider != null;
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
      size="md"
      title={current?.provider != null ? t("variables.providers.dialog.editTitle", { name: current.provider.name }) : t("variables.providers.dialog.addTitle")}
      description={t("variables.providers.dialog.description")}
      footer={
        <>
          <Button disabled={pending} onClick={onClose}>
            {t("variables.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending}>
            {editing ? t("variables.providers.dialog.submitEdit") : t("variables.providers.dialog.submitAdd")}
          </Button>
        </>
      }
    >
      {current !== null && shown !== null ? <ProviderForm key={shown.generation} formId={formId} target={current} kinds={kinds} onSubmit={onSubmit} onPending={setPending} /> : null}
    </Dialog>
  );
}

/**
 * The secret managers a variable can refer to (Vault, Doppler, Infisical), a section of the
 * server's Settings. Noust keeps the token, sealed, and fetches the value at every deploy: it
 * never stores a secret that lives in a manager, and a deploy whose reference does not resolve
 * stops before anything is built. The token is written here and never read back.
 */
export function SecretManagers() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  useDocumentTitle(t("variables.providers.documentTitle"), 1);
  const canManage = useCan("settings.manage");
  const providers = useQuery(secretProvidersQuery());
  const kinds = useQuery(secretProviderKindsQuery());
  const [editing, setEditing] = useState<ProviderTarget | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  const refresh = (): Promise<void> => queryClient.invalidateQueries({ queryKey: variableKeys.providers });

  const test = useMutation({
    mutationFn: (name: string) => request("post", "/api/secret-providers/{name}/test", { params: { name } }),
    onSuccess: (result) => {
      toast.success(t("variables.providers.testOk", { name: result.name }), { description: result.message });
    },
    onError: (error: unknown, name) => {
      reportActionError(t("variables.providers.testFailed", { name }), error);
    },
  });

  const save = async (values: Values): Promise<void> => {
    await request("put", "/api/secret-providers/{name}", {
      params: { name: values.name },
      body: {
        kind: values.kind,
        ...(values.address !== "" ? { address: values.address } : {}),
        settings: values.settings,
        ...(values.token !== "" ? { token: values.token } : {}),
        ...(values.team !== EVERY_TEAM ? { team: values.team } : {}),
      },
    });
    await refresh();
    setEditing(null);
    announce(t("variables.providers.saved", { name: values.name }));
  };

  const list = useMemo(() => providers.data?.providers ?? [], [providers.data]);
  const addable = canManage && kinds.data !== undefined;

  const columns: Column<SecretProvider>[] = [
    {
      id: "name",
      header: t("variables.providers.nameHeader"),
      card: "title",
      sortValue: (provider) => provider.name,
      cell: (provider) => <Mono truncate title={provider.name}>{provider.name}</Mono>,
    },
    { id: "kind", header: t("variables.providers.kindHeader"), width: "w-44", sortValue: (provider) => provider.kind, cell: (provider) => <span>{kindName(t, provider.kind)}</span> },
    {
      id: "address",
      header: t("variables.providers.addressHeader"),
      hideBelow: "md",
      cell: (provider) =>
        provider.address ? (
          <Mono truncate title={provider.address} className="max-w-64">
            {provider.address}
          </Mono>
        ) : (
          <span className="text-fg-muted">{t("variables.providers.hostedAddress")}</span>
        ),
    },
    {
      id: "team",
      header: t("variables.providers.teamHeader"),
      width: "w-40",
      hideBelow: "lg",
      cell: (provider) => (provider.team ? <Mono tone="muted">{provider.team}</Mono> : <span className="text-fg-muted">{t("variables.providers.everyTeam")}</span>),
    },
    {
      id: "token",
      header: t("variables.providers.tokenHeader"),
      width: "w-32",
      card: "status",
      cell: (provider) => <StatusPill appearance="inline" size="sm" state={provider.has_token ? "running" : "warning"} label={provider.has_token ? t("variables.providers.tokenSet") : t("variables.providers.tokenMissing")} />,
    },
  ];

  const rowActions = (provider: SecretProvider) => (
    <Menu align="end" trigger={<IconButton size="sm" label={t("variables.providers.actions", { name: provider.name })} icon={<ICONS.more />} tooltip={false} />}>
      <MenuItem icon={<PlugZap />} disabled={test.isPending} onClick={() => test.mutate(provider.name)}>
        {t("variables.providers.test")}
      </MenuItem>
      {canManage ? (
        <>
          <MenuItem icon={<Pencil />} onClick={() => setEditing({ provider })}>
            {t("variables.providers.edit")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem icon={<ICONS.delete />} destructive onClick={() => setRemoving(provider.name)}>
            {t("variables.providers.remove")}
          </MenuItem>
        </>
      ) : null}
    </Menu>
  );

  const add = addable ? (
    <SettingsPrimaryAction>
      <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setEditing({ provider: null })}>
        {t("variables.providers.add")}
      </Button>
    </SettingsPrimaryAction>
  ) : null;

  const caption = t("variables.providers.tableCaption");
  return (
    <div className="flex flex-col gap-6">
      <Section title={t("variables.providers.title")} icon={OBJECT_ICONS.secretManager} description={t.rich("variables.providers.description", { syntax: <Mono>{syntaxOf("NAME")}</Mono> })}>
        {add}
        <QueryState
          query={providers}
          label={t("variables.providers.queryLabel")}
          skeleton={<DataTable caption={caption} columns={columns} rows={[]} getRowId={(provider) => provider.name} density="compact" mobile="cards" skeletonRows={2} loading />}
          isEmpty={(data) => data.providers.length === 0}
          empty={
            <EmptyState
              variant="firstUse"
              level={3}
              icon={<KeyRound />}
              title={t("variables.providers.emptyTitle")}
              description={t("variables.providers.emptyDescription")}
              command="noust secrets add NAME --kind vault --address https://vault.example.com:8200 --token-stdin"
            />
          }
        >
          {() => <DataTable caption={caption} columns={columns} rows={list} getRowId={(provider) => provider.name} rowActions={rowActions} density="compact" mobile="cards" defaultSort={{ column: "name", direction: "ascending" }} />}
        </QueryState>
      </Section>
      <CommandHint command="noust secrets list" label={t("variables.shared.fromTerminal")} />

      {kinds.data !== undefined ? <ProviderDialog target={editing} kinds={kinds.data.kinds} onClose={() => setEditing(null)} onSubmit={save} /> : null}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        friction="simple"
        server={node}
        title={t("variables.providers.removeDialog.title", { name: removing ?? "" })}
        description={t("variables.providers.removeDialog.description")}
        actionLabel={t("variables.providers.removeDialog.action")}
        destructive
        onConfirm={async () => {
          if (removing === null) return;
          const name = removing;
          await request("delete", "/api/secret-providers/{name}", { params: { name } });
          await refresh();
          announce(t("variables.providers.removed", { name }));
        }}
      />
    </div>
  );
}
