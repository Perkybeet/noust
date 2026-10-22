import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { directoriesQuery } from "../../../api/queries/ldap";
import { samlProvidersQuery } from "../../../api/queries/saml";
import { linkIdentity, ssoAdminKeys, ssoConfigQuery, ssoIdentitiesQuery, unlinkIdentity } from "../../../api/queries/ssoAdmin";
import type { SsoIdentity } from "../../../api/queries/ssoAdmin";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { accountsQuery } from "../accounts/api";
import { focusFirstInvalid, splitErrors } from "../formErrors";
import { RowAction } from "../RowAction";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

function columnsFor(t: T): Column<SsoIdentity>[] {
  return [
    {
      id: "account",
      header: t("ssoAdmin.identities.column.account"),
      card: "title",
      cell: (identity) => <Mono tone="default">{identity.account}</Mono>,
    },
    {
      id: "provider",
      header: t("ssoAdmin.identities.column.provider"),
      cell: (identity) => <Mono tone="default">{identity.provider}</Mono>,
    },
    {
      id: "subject",
      header: t("ssoAdmin.identities.column.subject"),
      hideBelow: "md",
      cell: (identity) => <Mono tone="faint">{identity.subject}</Mono>,
    },
    {
      id: "email",
      header: t("ssoAdmin.identities.column.email"),
      hideBelow: "lg",
      cell: (identity) => (identity.email === null || identity.email === undefined ? <EmptyCell reason={t("ssoAdmin.identities.never")} /> : <Mono tone="default">{identity.email}</Mono>),
    },
    {
      id: "last",
      header: t("ssoAdmin.identities.column.lastSignIn"),
      hideBelow: "sm",
      cell: (identity) => <RelativeTime value={identity.last_login_at} fallback={t("ssoAdmin.identities.never")} />,
    },
  ];
}

const LINK_FIELDS = ["username", "provider", "subject"] as const;

/** Linking a person by hand: for someone the provider's first sign-in refused. */
function LinkDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const accounts = useQuery(accountsQuery());
  const config = useQuery(ssoConfigQuery());
  const saml = useQuery(samlProvidersQuery());
  const directories = useQuery(directoriesQuery());
  const [username, setUsername] = useState("");
  const [provider, setProvider] = useState("");
  const [subject, setSubject] = useState("");
  const [tried, setTried] = useState(false);
  const required = t("settings.shared.required");
  const link = useMutation({
    mutationFn: () => linkIdentity({ username, provider, subject: subject.trim() }),
    onSuccess: (linked) => {
      toast.success(t("ssoAdmin.identities.linkedToast", { account: linked.account, provider: linked.provider }));
      void queryClient.invalidateQueries({ queryKey: ssoAdminKeys.identities });
      onClose();
    },
  });
  const errors = splitErrors(link.error, LINK_FIELDS);
  const people = (accounts.data?.accounts ?? []).filter((account) => account.status === "active");
  // Whoever signs people in can be linked to by hand: a provider, a SAML provider, a directory.
  const providers = [
    ...(config.data?.providers ?? []),
    ...(saml.data?.providers ?? []),
    ...(directories.data?.directories ?? []),
  ];

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !link.isPending) onClose();
      }}
      size="md"
      title={t("ssoAdmin.identities.linkDialog.title")}
      description={t("ssoAdmin.identities.linkDialog.description")}
      footer={
        <>
          <Button disabled={link.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={link.isPending}>
            {t("ssoAdmin.identities.linkDialog.action")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (link.isPending) return;
          // The button is never off for a missing value: it says what is missing, where, and goes there.
          if (username === "" || provider === "" || subject.trim() === "") {
            setTried(true);
            focusFirstInvalid(formId);
            return;
          }
          link.mutate();
        }}
      >
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("ssoAdmin.identities.linkDialog.failed")} /> : null}
        <Field label={t("ssoAdmin.identities.linkDialog.account")} nativeLabel={false} error={errors.fields.username ?? (tried && username === "" ? required : undefined)}>
          <Select
            options={people.map((account) => ({ value: account.username, label: account.username }))}
            value={username === "" ? null : username}
            onValueChange={setUsername}
            mono
          />
        </Field>
        <Field label={t("ssoAdmin.identities.linkDialog.provider")} nativeLabel={false} error={errors.fields.provider ?? (tried && provider === "" ? required : undefined)}>
          <Select
            options={providers.map((item) => ({ value: item.name, label: item.label }))}
            value={provider === "" ? null : provider}
            onValueChange={setProvider}
          />
        </Field>
        <Field label={t("ssoAdmin.identities.linkDialog.subject")} description={t("ssoAdmin.identities.linkDialog.subjectHelp")} error={errors.fields.subject ?? (tried && subject.trim() === "" ? required : undefined)}>
          <Input mono autoComplete="off" spellCheck={false} value={subject} onValueChange={setSubject} />
        </Field>
      </form>
    </Dialog>
  );
}

/** The people linked to a provider, by issuer and subject, with unlinking and linking by hand. */
export function IdentitiesSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(ssoIdentitiesQuery());
  const [linking, setLinking] = useState(false);
  const [unlinking, setUnlinking] = useState<SsoIdentity | null>(null);
  const columns = columnsFor(t);
  const link = (
    <Button
      variant="secondary"
      icon={<ICONS.add aria-hidden="true" />}
      onClick={() => {
        setLinking(true);
      }}
    >
      {t("ssoAdmin.identities.link")}
    </Button>
  );

  return (
    <Section title={t("ssoAdmin.identities.title")} icon={OBJECT_ICONS.identity} description={t("ssoAdmin.identities.description")} actions={query.data !== undefined && query.data.identities.length > 0 ? link : undefined}>
      <QueryState
        query={query}
        label={t("ssoAdmin.loadingLabel")}
        skeleton={<DataTable caption={t("ssoAdmin.identities.tableCaption")} columns={columns} rows={[]} getRowId={(identity) => String(identity.id)} loading mobile="cards" skeletonRows={2} />}
        isEmpty={(data) => data.identities.length === 0}
        empty={<EmptyState variant="inline" title={t("ssoAdmin.identities.emptyTitle")} description={t("ssoAdmin.identities.emptyDescription")} action={link} />}
      >
        {(data) => (
          <DataTable
            caption={t("ssoAdmin.identities.tableCaption")}
            columns={columns}
            rows={data.identities}
            getRowId={(identity) => String(identity.id)}
            mobile="cards"
            rowActions={(identity) => (
              <RowAction
                label={t("ssoAdmin.identities.unlinkLabel", { account: identity.account, provider: identity.provider })}
                text={t("ssoAdmin.identities.unlink")}
                icon={<ICONS.delete />}
                onClick={() => {
                  setUnlinking(identity);
                }}
              />
            )}
          />
        )}
      </QueryState>
      {linking ? (
        <LinkDialog
          onClose={() => {
            setLinking(false);
          }}
        />
      ) : null}
      {unlinking !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setUnlinking(null);
          }}
          title={t("ssoAdmin.identities.unlinkDialog.title", { account: unlinking.account })}
          description={t("ssoAdmin.identities.unlinkDialog.description", { provider: unlinking.provider })}
          actionLabel={t("ssoAdmin.identities.unlinkDialog.action")}
          destructive
          onConfirm={async () => {
            await unlinkIdentity(unlinking.id);
            toast.success(t("ssoAdmin.identities.unlinkedToast", { account: unlinking.account }));
            void queryClient.invalidateQueries({ queryKey: ssoAdminKeys.identities });
          }}
        />
      ) : null}
    </Section>
  );
}
