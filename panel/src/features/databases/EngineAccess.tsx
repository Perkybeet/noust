import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { databaseKeys } from "../../api/queries/databases";
import type { DatabaseList, Engine } from "../../api/queries/databases";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";

export type ListingProblem = NonNullable<DatabaseList["problems"]>[number];

/**
 * The account Noust signs in to an engine with: tried by the server before it is saved, so a
 * wrong password is answered with the engine's own words and nothing changes.
 */
export function EngineAccountDialog({
  engine,
  displayName,
  open,
  onOpenChange,
}: {
  engine: string;
  displayName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [empty, setEmpty] = useState(false);
  // Redis has no administrative user, only the password every client presents.
  const hasUser = engine !== "redis";
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/databases/engines/{engine}/credentials", {
        params: { engine },
        body: { user: hasUser && user.trim() ? user.trim() : null, password: password || null },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.all });
      setPassword("");
      onOpenChange(false);
    },
  });
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const nothing = !(hasUser && user.trim()) && !password;
    setEmpty(nothing);
    if (!nothing) save.mutate();
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("databases.access.dialogTitle", { engine: displayName })}
      description={t("databases.access.dialogDescription", { engine: displayName })}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form="engine-account-form" variant="primary" loading={save.isPending}>
            {t("databases.access.save")}
          </Button>
        </>
      }
    >
      <form id="engine-account-form" noValidate onSubmit={submit} className="flex flex-col gap-5">
        {hasUser ? (
          <Field label={t("databases.access.user")} description={t("databases.access.userHelp")}>
            <Input mono value={user} onValueChange={(value: string) => setUser(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
        ) : null}
        <Field label={t("databases.access.password")} description={t("databases.access.passwordHelp")} error={empty ? t("databases.access.nothing") : undefined}>
          <Input mono type="password" value={password} onValueChange={(value: string) => setPassword(value)} autoComplete="new-password" spellCheck={false} />
        </Field>
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("databases.access.saveFailed", { engine: displayName })} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * The engines whose databases could not be read, above the list: what failed, the fix, and the
 * engine's own answer verbatim. An engine that signs in with a stored account offers to store
 * one, the fix for a refused sign-in.
 */
export function ListingProblemsNotice({ problems, engines }: { problems: readonly ListingProblem[]; engines: readonly Engine[] | undefined }) {
  const t = useT();
  const [storing, setStoring] = useState<ListingProblem | null>(null);
  if (problems.length === 0) return null;
  const first = problems[0];
  const title =
    problems.length === 1 && first !== undefined
      ? t("databases.access.title", { engine: first.display_name })
      : t("databases.access.titleMany", { count: problems.length });
  const storable = (problem: ListingProblem): boolean =>
    problem.access && (engines?.find((engine) => engine.name === problem.engine)?.stored_account ?? false);
  return (
    <>
      <Notice tone="error" title={title}>
        <div className="flex flex-col gap-3">
          {problems.map((problem) => (
            <div key={problem.engine} className="flex min-w-0 flex-col gap-2">
              {problem.hint ? <p>{problem.hint}</p> : null}
              <p className="text-fg-muted">{t("databases.access.body", { engine: problem.display_name })}</p>
              <SystemOutput label={t("databases.access.output", { engine: problem.display_name })}>{problem.output || problem.message}</SystemOutput>
              {storable(problem) ? (
                <div>
                  <Button size="sm" onClick={() => setStoring(problem)}>
                    {t("databases.access.storeAccount")}
                  </Button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </Notice>
      {storing !== null ? (
        <EngineAccountDialog
          key={storing.engine}
          engine={storing.engine}
          displayName={storing.display_name}
          open
          onOpenChange={(open) => {
            if (!open) setStoring(null);
          }}
        />
      ) : null}
    </>
  );
}
