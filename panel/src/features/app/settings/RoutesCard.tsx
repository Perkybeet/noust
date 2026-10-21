import { Pencil, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import type { PolicyFields } from "../../../api/queries/network";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { RowList } from "../../../components/ui/RowList";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { REDIRECT_CODES, draftOfRoute, errorsOfRoute, normalPath, routeOf, withRoute, withoutRoute } from "./routes";
import type { PolicyRoute, RouteAuth, RouteDraft, RouteErrors, RouteKind } from "./routes";
import { SETTINGS_ICONS } from "./objectIcons";

type Route = PolicyFields["routes"][number];

interface RoutesCardProps {
  domain: string;
  /** The routes as the form holds them. */
  routes: readonly Route[];
  /** The server's refusal of the routes, if it gave one. */
  error: string | undefined;
  onChange: (routes: Route[]) => void;
}

/** What a route does, in one line a person can read down a list. */
function summary(route: Route, t: T): string {
  const parts: string[] = [];
  if (route.kind === "proxy") {
    parts.push(route.strip ? t("network.access.routes.summary.proxyStrip", { port: route.port }) : t("network.access.routes.summary.proxy", { port: route.port }));
  } else if (route.kind === "redirect") {
    parts.push(
      route.keep_path
        ? t("network.access.routes.summary.redirect", { code: route.code, to: route.to })
        : t("network.access.routes.summary.redirectDrop", { code: route.code, to: route.to }),
    );
  } else {
    parts.push(t("network.access.routes.summary.site"));
  }
  if (route.auth === "password") parts.push(t("network.access.routes.summary.password"));
  if (route.auth === "open") parts.push(t("network.access.routes.summary.open"));
  const headers = Object.keys(route.headers);
  if (headers.length > 0) parts.push(t("network.access.routes.summary.headers", { names: headers.join(", ") }));
  return parts.join(" · ");
}

interface RouteDialogProps {
  /** The route being changed, or null for a new one. */
  route: Route | null;
  /** The paths already routed, which a new route may not repeat. */
  others: readonly string[];
  domain: string;
  onClose: () => void;
  onApply: (route: PolicyRoute) => void;
}

function RouteDialog({ route, others, domain, onClose, onApply }: RouteDialogProps) {
  const t = useT();
  const formId = useId();
  const [draft, setDraft] = useState<RouteDraft>(() => draftOfRoute(route ?? undefined));
  const [errors, setErrors] = useState<RouteErrors>({});
  const edit = <K extends keyof RouteDraft>(field: K, value: RouteDraft[K]): void => {
    setDraft((previous) => ({ ...previous, [field]: value }));
  };
  const message = (field: keyof RouteDraft): string | undefined => {
    const key = errors[field];
    return key === undefined ? undefined : t(key);
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found = errorsOfRoute(draft, route === null ? others : others.filter((path) => path !== route.path));
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    onApply(routeOf(draft));
    onClose();
  };

  const kinds: { value: RouteKind; label: string; description: string }[] = [
    { value: "site", label: t("network.access.routes.kinds.site"), description: t("network.access.routes.kinds.siteHelp") },
    { value: "proxy", label: t("network.access.routes.kinds.proxy"), description: t("network.access.routes.kinds.proxyHelp") },
    { value: "redirect", label: t("network.access.routes.kinds.redirect"), description: t("network.access.routes.kinds.redirectHelp") },
  ];

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={route === null ? t("network.access.routes.addTitle") : t("network.access.routes.editTitle", { path: route.path })}
      description={t("network.access.routes.dialogDescription", { domain })}
      footer={
        <>
          <Button onClick={onClose}>{t("common.confirmDialog.cancel")}</Button>
          <Button variant="primary" type="submit" form={formId}>
            {t("network.access.routes.apply")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("network.access.routes.path")} description={t("network.access.routes.pathHelp")} error={message("path")}>
          <Input mono value={draft.path} onValueChange={(value: string) => edit("path", value)} disabled={route !== null} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <ChoiceCards<RouteKind> legend={t("network.access.routes.kind")} options={kinds} value={draft.kind} onValueChange={(value) => edit("kind", value)} />
        {draft.kind === "proxy" ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("network.access.routes.port")} description={t("network.access.routes.portHelp")} error={message("port")}>
              <Input mono inputMode="numeric" value={draft.port} onValueChange={(value: string) => edit("port", value)} autoComplete="off" />
            </Field>
            <Checkbox
              label={t("network.access.routes.strip")}
              description={t("network.access.routes.stripHelp")}
              checked={draft.strip}
              onCheckedChange={(checked) => edit("strip", checked)}
            />
          </div>
        ) : null}
        {draft.kind === "redirect" ? (
          <div className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label={t("network.access.routes.to")} description={t("network.access.routes.toHelp")} error={message("to")} className="sm:col-span-2">
                <Input mono value={draft.to} onValueChange={(value: string) => edit("to", value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
              </Field>
              <Field label={t("network.access.routes.code")} nativeLabel={false}>
                <Select
                  aria-label={t("network.access.routes.code")}
                  value={draft.code}
                  onValueChange={(value) => edit("code", value)}
                  options={REDIRECT_CODES.map((code) => ({ value: String(code), label: t(`network.access.routes.codes.c${String(code)}` as "network.access.routes.codes.c301") }))}
                />
              </Field>
            </div>
            <Checkbox
              label={t("network.access.routes.keepPath")}
              description={t("network.access.routes.keepPathHelp")}
              checked={draft.keepPath}
              onCheckedChange={(checked) => edit("keepPath", checked)}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <Field label={t("network.access.routes.auth")} description={t("network.access.routes.authHelp")} error={message("auth")} nativeLabel={false}>
              <Select<RouteAuth>
                aria-label={t("network.access.routes.auth")}
                value={draft.auth}
                onValueChange={(value) => edit("auth", value)}
                options={[
                  { value: "inherit", label: t("network.access.routes.authInherit") },
                  { value: "password", label: t("network.access.routes.authPassword") },
                  { value: "open", label: t("network.access.routes.authOpen") },
                ]}
              />
            </Field>
            <Field label={t("network.access.routes.headers")} description={t("network.access.routes.headersHelp")} error={message("headers")}>
              <Textarea mono rows={3} wrap="off" value={draft.headers} onChange={(event) => edit("headers", event.target.value)} autoComplete="off" spellCheck={false} />
            </Field>
          </div>
        )}
      </form>
    </Dialog>
  );
}

/**
 * What happens at prefixes of the site: a password asked or lifted at `/admin`, a header on
 * `/api`, another service behind `/blog`, a redirect from `/docs`. Part of the access form: a
 * change is a draft until the form is saved, and the server checks the whole of it and has the
 * web server test the new site.
 */
export function RoutesCard({ domain, routes, error, onChange }: RoutesCardProps) {
  const t = useT();
  const [dialog, setDialog] = useState<{ route: Route | null } | null>(null);
  const [asked, setAsked] = useState(0);

  return (
    <Card
      icon={SETTINGS_ICONS.routes}
      title={t("network.access.routes.title")}
      description={t("network.access.routes.description")}
      actions={
        <Button
          size="sm"
          icon={<Plus aria-hidden="true" />}
          onClick={() => {
            setAsked((count) => count + 1);
            setDialog({ route: null });
          }}
        >
          {t("network.access.routes.add")}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        {error !== undefined ? (
          <Notice tone="error" title={t("network.access.routes.refused")}>
            {error}
          </Notice>
        ) : null}
        {routes.length === 0 ? (
          <EmptyState variant="inline" title={t("network.access.routes.empty")} />
        ) : (
          <RowList aria-label={t("network.access.routes.listLabel", { domain })}>
            {routes.map((route) => (
              <RowList.Item
                key={route.path}
                actions={
                  <>
                    <IconButton label={t("network.access.routes.edit", { path: route.path })} icon={<Pencil />} size="sm" onClick={() => setDialog({ route })} />
                    <IconButton label={t("network.access.routes.remove", { path: route.path })} icon={<Trash2 />} size="sm" onClick={() => onChange(withoutRoute(routes, route.path))} />
                  </>
                }
              >
                <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                  <Mono truncate>{route.path}</Mono>
                  <Badge tone={route.kind === "site" ? "neutral" : "accent"}>{t(`network.access.routes.kinds.${route.kind}` as "network.access.routes.kinds.site")}</Badge>
                  <span className="min-w-0 text-13 text-fg-muted">{summary(route, t)}</span>
                </span>
              </RowList.Item>
            ))}
          </RowList>
        )}
      </div>
      {dialog !== null ? (
        <RouteDialog
          key={`${dialog.route?.path ?? "new"}-${String(asked)}`}
          route={dialog.route}
          others={routes.map((route) => route.path)}
          domain={domain}
          onClose={() => setDialog(null)}
          onApply={(route) => {
            const replaced = dialog.route === null ? routes : withoutRoute(routes, dialog.route.path);
            onChange(withRoute(replaced, { ...route, path: normalPath(route.path) }));
          }}
        />
      ) : null}
    </Card>
  );
}
