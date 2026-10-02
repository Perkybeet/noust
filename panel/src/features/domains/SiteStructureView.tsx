import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Copy, CornerDownRight, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { siteStructureQuery, siteTopologyQuery } from "../../api/queries/sites";
import type { SiteEditOp, SiteLocation, SiteNote, SiteRawDirective, SiteServer, SiteStructure, SiteTopology, SiteUpstream } from "../../api/queries/sites";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section, Sections } from "../../components/page/Section";
import { Subsection } from "../../components/page/Subsection";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { Switch } from "../../components/ui/Switch";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import {
  addLocationOp,
  changedIds,
  isBlock,
  joinArgs,
  listenChip,
  listenGroups,
  locationArgs,
  locationLabel,
  locationsInOrder,
  matchKind,
  moveOp,
  otherDirectives,
  removeLocationOp,
  settingOps,
  settingText,
  siblingsOf,
  splitArgs,
  websocketOps,
} from "./siteStructure";
import type { InlineSetting, LocationTemplate, NewLocation } from "./siteStructure";
import { NotParsed, OlderNoust, ViewLoading, isMissingRoute } from "./SiteViewStates";

export interface SiteStructureViewProps {
  site: string;
  webserver: string;
  /** The draft: the text every view shares. */
  text: string;
  /** Whether the draft differs from the saved file, so changed elements are marked. */
  dirty: boolean;
  /** Applies operations to the draft through `/config/edit`; the page owns the draft. */
  onEdit: (ops: SiteEditOp[]) => void;
  /** An edit is on its way: controls wait for it, so the next one names current ids. */
  editing: boolean;
  /** Why the last edit was refused, in the backend's words. */
  editError: unknown;
  onGoToText: (line?: number) => void;
}

/** What every row of the view needs to edit and to say what changed. */
interface Ctx {
  kind: string;
  structure: SiteStructure;
  changed: ReadonlySet<string>;
  onEdit: (ops: SiteEditOp[]) => void;
  editing: boolean;
  onGoToText: (line?: number) => void;
}

/**
 * A site's configuration as what it is: one card per server (its ports, names, certificate,
 * common settings), its locations in the order nginx tries them with their most used
 * settings in line, the upstreams, and everything else as written. Every change is an
 * operation the backend applies to the draft text, so the Text and Diagram views see it and
 * the save bar counts it; nothing here writes the file.
 */
export function SiteStructureView({ site, webserver, text, dirty, onEdit, editing, editError, onGoToText }: SiteStructureViewProps) {
  const t = useT();
  const draft = useQuery(siteStructureQuery(site, dirty ? text : null));
  const saved = useQuery({ ...siteStructureQuery(site, null), enabled: dirty });
  const topology = useQuery(siteTopologyQuery(site));

  if (draft.isError) {
    if (isMissingRoute(draft.error)) return <OlderNoust onText={() => onGoToText()} />;
    return <ErrorBlock error={draft.error} title={t("domains.siteViews.couldNotRead", { site })} onRetry={() => void draft.refetch()} retrying={draft.isRefetching} />;
  }
  if (draft.data === undefined) return <ViewLoading />;
  if (draft.data.error) return <NotParsed failure={draft.data.error} webserver={webserver} onGoToText={onGoToText} />;
  const structure = draft.data.structure;
  if (!structure) return <ViewLoading />;

  const changed = dirty ? changedIds(saved.data?.structure ?? null, structure) : new Set<string>();
  const ctx: Ctx = {
    kind: structure.kind,
    structure,
    changed,
    onEdit,
    editing,
    onGoToText,
  };
  const certificates = topology.data?.certificates ?? [];

  return (
    <div aria-label={t("domains.siteStructure.label", { site })} role="region" className="flex min-w-0 flex-col gap-6">
      {editError ? <ErrorBlock live compact error={editError} title={t("domains.siteViews.editFailed")} /> : null}
      <p className="text-13 text-fg-muted">{t("domains.siteStructure.draftNote", { webserver })}</p>
      <Sections>
        {structure.notes.filter((note) => note.after === null || note.after === undefined).length > 0 ? (
          <Notes notes={structure.notes.filter((note) => note.after === null || note.after === undefined)} />
        ) : null}
        {structure.servers.map((server) => (
          <ServerCard key={server.id} server={server} ctx={ctx} certificate={certificates.find((entry) => entry.path === server.tls?.certificate) ?? null} />
        ))}
        {structure.upstreams.length > 0 ? (
          <Section title={t("domains.siteStructure.upstreamsTitle")} description={t("domains.siteStructure.upstreamsDescription")}>
            <div className="grid gap-4 lg:grid-cols-2">
              {structure.upstreams.map((upstream) => (
                <UpstreamCard key={upstream.id} upstream={upstream} ctx={ctx} />
              ))}
            </div>
          </Section>
        ) : null}
        {structure.directives.length > 0 ? (
          <Section title={t("domains.siteStructure.otherTitle")} description={t("domains.siteStructure.otherDescription")}>
            <Card padding="sm">
              <RawDirectives directives={structure.directives} parent="" ctx={ctx} />
            </Card>
          </Section>
        ) : null}
        {structure.includes.length > 0 ? (
          <Section title={t("domains.siteStructure.includesTitle")}>
            <ul className="flex flex-col gap-2 text-13">
              {structure.includes.map((include) => (
                <li key={include.id} className="flex min-w-0 flex-col gap-1">
                  {include.error ? (
                    <span className="text-fg-muted">
                      {t.rich("domains.siteStructure.includeError", {
                        pattern: <Mono>{include.pattern}</Mono>,
                        error: include.error,
                      })}
                    </span>
                  ) : (
                    include.files.map((file) => (
                      <Mono key={file.path} truncate title={file.path}>
                        {file.path}
                      </Mono>
                    ))
                  )}
                </li>
              ))}
            </ul>
          </Section>
        ) : null}
      </Sections>
    </div>
  );
}

/** The operator's comments, verbatim: they are the file's own notes. */
function Notes({ notes, lines }: { notes?: readonly SiteNote[]; lines?: readonly string[] }) {
  const t = useT();
  const text = (lines ?? notes?.map((note) => note.text) ?? []).join("\n").trim();
  if (text === "") return null;
  return (
    <p aria-label={t("domains.siteStructure.noteLabel")} className="border-l-2 border-border-strong pl-3 text-13 whitespace-pre-line text-fg-muted">
      {text}
    </p>
  );
}

function ChangedBadge({ id, ctx }: { id: string; ctx: Ctx }) {
  const t = useT();
  return ctx.changed.has(id) ? <Badge tone="accent">{t("domains.siteViews.changed")}</Badge> : null;
}

/**
 * A text field that edits the draft when the operator is done with it (Enter or leaving it),
 * not on every key: each change is an operation and a round trip. Escape puts back what the
 * file says. Keyed by the file's value by its caller, so a new draft resets it.
 */
function CommitField({
  label,
  description,
  value,
  onCommit,
  disabled,
  mono = true,
  className,
}: {
  label: ReactNode;
  description?: ReactNode;
  value: string;
  /** Returns an error to show, or null when the change went to the draft. */
  onCommit: (text: string) => string | null;
  disabled: boolean;
  mono?: boolean;
  className?: string;
}) {
  const [text, setText] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const commit = (): void => {
    if (text === value) return;
    setError(onCommit(text));
  };
  return (
    <Field label={label} {...(description !== undefined ? { description } : {})} {...(className !== undefined ? { className } : {})} error={error} optional>
      <Input
        size="sm"
        mono={mono}
        value={text}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => setText(event.currentTarget.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            commit();
          } else if (event.key === "Escape") {
            setText(value);
            setError(null);
          }
        }}
      />
    </Field>
  );
}

function ServerCard({ server, ctx, certificate }: { server: SiteServer; ctx: Ctx; certificate: SiteTopology["certificates"][number] | null }) {
  const t = useT();
  const [adding, setAdding] = useState(false);
  const name = server.names[0];
  const nginx = ctx.kind === "nginx";
  const groups = listenGroups(server);
  const ordered = locationsInOrder(server);
  const notesAfter = new Map<string, SiteNote[]>();
  for (const note of server.notes) {
    if (note.after) notesAfter.set(note.after, [...(notesAfter.get(note.after) ?? []), note]);
  }
  const fieldDirectives = nginx ? new Set(["server_name", "client_max_body_size"]) : new Set<string>();
  const rest = server.directives.filter((directive) => !fieldDirectives.has(directive.name));
  const bodySize = server.directives.find((directive) => directive.name === "client_max_body_size");

  return (
    <Card
      level={2}
      title={
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          {name !== undefined
            ? t.rich("domains.siteStructure.serverTitle", {
                name: <Mono>{name}</Mono>,
              })
            : t("domains.siteStructure.serverWithoutName")}
          <ChangedBadge id={server.id} ctx={ctx} />
        </span>
      }
      actions={
        <Button size="sm" icon={<Plus aria-hidden="true" />} disabled={ctx.editing} onClick={() => setAdding(true)}>
          {t("domains.siteStructure.addLocation")}
        </Button>
      }
    >
      <div className="flex min-w-0 flex-col gap-5">
        <Notes lines={server.comments} />
        <dl className="grid gap-x-6 gap-y-2 text-13 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-1">
            <dt className="text-12 text-fg-muted">{t("domains.siteStructure.listensOn")}</dt>
            <dd className="flex flex-wrap items-center gap-1.5">
              {groups.map((group) => (
                <Badge key={`${group.port}|${String(group.tls)}`} mono>
                  {listenChip(group)}
                </Badge>
              ))}
            </dd>
          </div>
          {server.tls?.certificate ? (
            <div className="flex min-w-0 flex-col gap-1">
              <dt className="text-12 text-fg-muted">{t("domains.siteStructure.certificateLabel")}</dt>
              <dd className="flex min-w-0 flex-col">
                <Mono truncate title={server.tls.certificate}>
                  {server.tls.certificate}
                </Mono>
                <span
                  className={cx("text-12", certificate?.days_left !== null && certificate?.days_left !== undefined && certificate.days_left < 0 ? "text-fail" : "text-fg-muted")}
                >
                  {certificate?.days_left === null || certificate?.days_left === undefined
                    ? t("domains.siteStructure.expiryUnknown")
                    : certificate.days_left < 0
                      ? t("domains.siteStructure.expired", {
                          count: -certificate.days_left,
                        })
                      : t("domains.siteStructure.expiresIn", {
                          count: certificate.days_left,
                        })}
                </span>
              </dd>
            </div>
          ) : null}
        </dl>
        {nginx ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <CommitField
              key={`names:${server.names.join(" ")}`}
              label={t("domains.siteStructure.namesField")}
              description={t("domains.siteStructure.namesHelp")}
              value={server.names.join(" ")}
              disabled={ctx.editing}
              onCommit={(value) => {
                const args = splitArgs(value.trim());
                if (args === null) return t("domains.siteStructure.unclosedQuote");
                ctx.onEdit(
                  args.length === 0
                    ? [
                        {
                          op: "remove_directive",
                          parent: server.id,
                          name: "server_name",
                        },
                      ]
                    : [
                        {
                          op: "set_directive",
                          parent: server.id,
                          name: "server_name",
                          args,
                        },
                      ],
                );
                return null;
              }}
            />
            <CommitField
              key={`body:${bodySize?.text ?? ""}`}
              label={t("domains.siteStructure.serverBodyField")}
              description={t("domains.siteStructure.serverBodyHelp")}
              value={bodySize ? joinArgs(bodySize.args) : ""}
              disabled={ctx.editing}
              onCommit={(value) => {
                const args = splitArgs(value.trim());
                if (args === null) return t("domains.siteStructure.unclosedQuote");
                ctx.onEdit(
                  args.length === 0
                    ? bodySize
                      ? [{ op: "remove_directive", target: bodySize.id }]
                      : []
                    : [
                        {
                          op: "set_directive",
                          parent: server.id,
                          name: "client_max_body_size",
                          args,
                        },
                      ],
                );
                return null;
              }}
            />
          </div>
        ) : null}
        {server.returns ? (
          <p className="text-13 text-fg-muted">
            {t.rich("domains.siteStructure.returnsNote", {
              code: <Mono>{String(server.returns.code ?? "")}</Mono>,
              destination: <Mono>{server.returns.destination ?? ""}</Mono>,
            })}
          </p>
        ) : null}
        <Subsection
          title={nginx ? t("domains.siteStructure.locationsTitle") : t("domains.siteStructure.locationsTitleApache")}
          {...(nginx ? { description: t("domains.siteStructure.locationsDescription") } : {})}
        >
          {ordered.length === 0 ? (
            <p className="text-13 text-fg-muted">{t("domains.siteStructure.noLocations")}</p>
          ) : (
            <ol className="flex flex-col divide-y divide-border border-y border-border">
              {ordered.map(({ location, depth }) => (
                <LocationRow key={location.id} location={location} depth={depth} server={server} ctx={ctx} notes={notesAfter.get(location.id) ?? []} />
              ))}
            </ol>
          )}
        </Subsection>
        {rest.length > 0 ? (
          <Disclosure label={t("domains.siteStructure.serverDirectives")}>
            <RawDirectives directives={rest} parent={server.id} ctx={ctx} />
          </Disclosure>
        ) : null}
      </div>
      {adding ? <AddLocationDialog server={server} ctx={ctx} onClose={() => setAdding(false)} /> : null}
    </Card>
  );
}

/** Where a location sends a request, in words; an upstream is a link to its card. */
function Target({ location }: { location: SiteLocation }) {
  const t = useT();
  const target = location.target;
  switch (target.kind) {
    case "proxy":
      if (target.upstream) {
        const upstream = target.upstream;
        return (
          <button
            type="button"
            aria-label={t("domains.siteStructure.showUpstream", { upstream })}
            onClick={() => {
              const card = document.getElementById(upstreamAnchor(upstream));
              card?.scrollIntoView({ block: "center" });
              card?.focus();
            }}
            className="inline-flex min-h-6 cursor-pointer items-center rounded-chip border border-border bg-bg-sunken px-1.5 text-12 text-accent-fg hover:border-border-strong"
          >
            {t.rich("domains.siteStructure.targetUpstream", {
              upstream: <Mono>{upstream}</Mono>,
            })}
          </button>
        );
      }
      return (
        <span>
          {t.rich("domains.siteStructure.targetProxy", {
            url: <Mono>{target.url ?? target.address ?? ""}</Mono>,
          })}
        </span>
      );
    case "static":
      return (
        <span>
          {t.rich("domains.siteStructure.targetStatic", {
            directory: <Mono>{target.alias ?? target.root ?? ""}</Mono>,
          })}
        </span>
      );
    case "return":
      return (
        <span>
          {t.rich("domains.siteStructure.targetReturn", {
            code: <Mono>{String(target.code ?? "")}</Mono>,
            destination: <Mono>{(target.destination ?? "").trim()}</Mono>,
          })}
        </span>
      );
    case "fastcgi":
      return (
        <span>
          {t.rich("domains.siteStructure.targetFastcgi", {
            address: <Mono>{target.address ?? ""}</Mono>,
          })}
        </span>
      );
    default:
      return <span>{location.settings.deny ? t("domains.siteStructure.targetDeny") : t("domains.siteStructure.targetOther")}</span>;
  }
}

function upstreamAnchor(name: string): string {
  return `site-upstream-${name.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

function LocationRow({ location, depth, server, ctx, notes }: { location: SiteLocation; depth: number; server: SiteServer; ctx: Ctx; notes: readonly SiteNote[] }) {
  const t = useT();
  const headingId = useId();
  const [duplicating, setDuplicating] = useState(false);
  const label = locationLabel(location);
  const nginx = ctx.kind === "nginx";
  const proxied = location.target.kind === "proxy" || location.target.kind === "fastcgi";
  const block = isBlock(location);
  const siblings = siblingsOf(server, location.id);
  const up = block ? moveOp(siblings, location, "up") : null;
  const down = block ? moveOp(siblings, location, "down") : null;
  const others = nginx ? otherDirectives(location) : location.directives;

  const commitSetting =
    (name: InlineSetting) =>
    (value: string): string | null => {
      const ops = settingOps(location, name, value);
      if (ops === null) return t("domains.siteStructure.unclosedQuote");
      ctx.onEdit(ops);
      return null;
    };
  const field = (name: InlineSetting, labelText: string, help: string) => {
    const value = settingText(location, name);
    return <CommitField key={`${name}:${value}`} label={labelText} description={help} value={value} disabled={ctx.editing} onCommit={commitSetting(name)} />;
  };
  const buffering = settingText(location, "buffering");

  return (
    <li>
      <div role="group" aria-labelledby={headingId} className={cx("flex min-w-0 flex-col gap-3 py-4", depth > 0 && "pl-6")}>
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
          <Badge>{t(`domains.siteStructure.match.${matchKind(location)}`)}</Badge>
          <span id={headingId} className="min-w-0 text-14 font-medium text-fg">
            <Mono>{label}</Mono>
          </span>
          <span className="flex min-w-0 items-center gap-1.5 text-13 text-fg-muted">
            <span className="shrink-0">{t("domains.siteStructure.goesTo")}</span>
            <Target location={location} />
          </span>
          <ChangedBadge id={location.id} ctx={ctx} />
          <span className="ml-auto">
            <Menu
              align="end"
              trigger={<IconButton size="sm" label={t("domains.actionsFor", { name: label })} icon={<MoreHorizontal aria-hidden="true" />} disabled={ctx.editing} />}
            >
              {block ? (
                <MenuItem icon={<Copy aria-hidden="true" />} onClick={() => setDuplicating(true)}>
                  {t("domains.siteStructure.actions.duplicate")}
                </MenuItem>
              ) : null}
              {up ? (
                <MenuItem icon={<ArrowUp aria-hidden="true" />} onClick={() => ctx.onEdit([up])}>
                  {t("domains.siteStructure.actions.moveUp")}
                </MenuItem>
              ) : null}
              {down ? (
                <MenuItem icon={<ArrowDown aria-hidden="true" />} onClick={() => ctx.onEdit([down])}>
                  {t("domains.siteStructure.actions.moveDown")}
                </MenuItem>
              ) : null}
              {block || up || down ? <MenuSeparator /> : null}
              <MenuItem icon={<Trash2 aria-hidden="true" />} destructive onClick={() => ctx.onEdit([removeLocationOp(location)])}>
                {t("domains.siteStructure.actions.remove")}
              </MenuItem>
            </Menu>
          </span>
        </div>
        <Notes lines={location.comments} />
        {nginx && block ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {proxied ? field("read_timeout", t("domains.siteStructure.readTimeoutField"), t("domains.siteStructure.readTimeoutHelp")) : null}
            {field("client_max_body_size", t("domains.siteStructure.bodyField"), t("domains.siteStructure.bodyHelp"))}
            {field("limit_req", t("domains.siteStructure.rateLimitField"), t("domains.siteStructure.rateLimitHelp"))}
            {location.target.kind === "proxy" ? (
              <div className="flex min-w-0 flex-col gap-4">
                <Field label={t("domains.siteStructure.bufferingField")} nativeLabel={false} optional>
                  <Select<"default" | "on" | "off">
                    size="sm"
                    disabled={ctx.editing}
                    value={buffering === "on" ? "on" : buffering === "off" ? "off" : "default"}
                    onValueChange={(next) => {
                      const ops = settingOps(location, "buffering", next === "default" ? "" : next);
                      if (ops !== null) ctx.onEdit(ops);
                    }}
                    options={[
                      {
                        value: "default",
                        label: t("domains.siteStructure.bufferingDefault"),
                      },
                      {
                        value: "on",
                        label: t("domains.siteStructure.bufferingOn"),
                      },
                      {
                        value: "off",
                        label: t("domains.siteStructure.bufferingOff"),
                      },
                    ]}
                  />
                </Field>
                <Switch
                  label={t("domains.siteStructure.websocketSwitch")}
                  checked={location.settings.websocket}
                  disabled={ctx.editing}
                  onCheckedChange={(on) => ctx.onEdit(websocketOps(location, on))}
                />
              </div>
            ) : null}
          </div>
        ) : null}
        {notes.length > 0 ? <Notes notes={notes} /> : null}
        {others.length > 0 || block ? (
          <Disclosure
            label={t("domains.siteStructure.moreDirectives", {
              count: others.length,
            })}
          >
            <RawDirectives directives={others} parent={block ? location.id : null} ctx={ctx} />
          </Disclosure>
        ) : null}
        {duplicating ? <DuplicateDialog location={location} server={server} ctx={ctx} onClose={() => setDuplicating(false)} /> : null}
      </div>
    </li>
  );
}

/**
 * Directives as written, each editable: its arguments in one line, removable; a block of its
 * own (an `if`, a `map`) shown whole with the way to its line, since only the text edits it.
 * `parent` null: the element takes no new directives (an Apache rule).
 */
function RawDirectives({ directives, parent, ctx, readOnly = false }: { directives: readonly SiteRawDirective[]; parent: string | null; ctx: Ctx; readOnly?: boolean }) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {directives.length > 0 ? (
        <ul className="flex min-w-0 flex-col gap-3">
          {directives.map((directive) => (
            <li key={directive.id} className="flex min-w-0 flex-col gap-1.5">
              <Notes lines={directive.comments} />
              {directive.block || readOnly ? (
                <div className="flex min-w-0 flex-col gap-1.5">
                  <Mono className="block max-h-48 overflow-auto rounded-control border border-border bg-bg-sunken px-3 py-2 text-12 whitespace-pre scroll-thin">
                    {directive.text}
                  </Mono>
                  <div className="flex items-center gap-3 text-12 text-fg-muted">
                    {directive.block ? <span>{t("domains.siteStructure.blockDirective")}</span> : null}
                    <Button size="sm" variant="ghost" icon={<CornerDownRight aria-hidden="true" />} onClick={() => ctx.onGoToText(directive.line)}>
                      {t("domains.siteStructure.goToLine", {
                        line: directive.line,
                      })}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex min-w-0 items-end gap-2">
                  <CommitField
                    key={directive.text}
                    className="flex-1"
                    label={<Mono>{directive.name}</Mono>}
                    value={joinArgs(directive.args)}
                    disabled={ctx.editing}
                    onCommit={(value) => {
                      const args = splitArgs(value.trim());
                      if (args === null) return t("domains.siteStructure.unclosedQuote");
                      ctx.onEdit([{ op: "set_directive", target: directive.id, args }]);
                      return null;
                    }}
                  />
                  <IconButton
                    size="sm"
                    label={t("domains.siteStructure.removeDirective", {
                      name: directive.name,
                    })}
                    icon={<Trash2 aria-hidden="true" />}
                    disabled={ctx.editing}
                    onClick={() => ctx.onEdit([{ op: "remove_directive", target: directive.id }])}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {parent !== null && !readOnly ? <AddDirective parent={parent} ctx={ctx} /> : null}
    </div>
  );
}

function AddDirective({ parent, ctx }: { parent: string; ctx: Ctx }) {
  const t = useT();
  const [name, setName] = useState("");
  const [args, setArgs] = useState("");
  const [error, setError] = useState<{ name?: string; args?: string }>({});
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const parsed = splitArgs(args.trim());
    const next: { name?: string; args?: string } = {};
    if (name.trim() === "") next.name = t("domains.siteStructure.addDirectiveNameRequired");
    if (parsed === null) next.args = t("domains.siteStructure.unclosedQuote");
    setError(next);
    if (next.name !== undefined || parsed === null) return;
    ctx.onEdit([{ op: "add_directive", parent, name: name.trim(), args: parsed }]);
    setName("");
    setArgs("");
  };
  return (
    <form onSubmit={submit} className="flex min-w-0 flex-wrap items-start gap-2">
      <Field label={t("domains.siteStructure.addDirectiveName")} error={error.name} className="w-44">
        <Input mono size="sm" value={name} spellCheck={false} autoComplete="off" onChange={(event) => setName(event.currentTarget.value)} />
      </Field>
      <Field label={t("domains.siteStructure.addDirectiveArgs")} error={error.args} optional className="min-w-48 flex-1">
        <Input mono size="sm" value={args} spellCheck={false} autoComplete="off" onChange={(event) => setArgs(event.currentTarget.value)} />
      </Field>
      <Button type="submit" size="sm" icon={<Plus aria-hidden="true" />} disabled={ctx.editing} className="mt-6">
        {t("domains.siteStructure.addDirective")}
      </Button>
    </form>
  );
}

function UpstreamCard({ upstream, ctx }: { upstream: SiteUpstream; ctx: Ctx }) {
  const t = useT();
  const readOnly = Boolean(upstream.source);
  return (
    // The target chip of a location scrolls here and focuses it.
    <div id={upstreamAnchor(upstream.name)} tabIndex={-1} className="min-w-0 rounded-card">
      <Card
        level={3}
        padding="sm"
        title={
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            {t.rich("domains.siteStructure.upstreamTitle", {
              name: <Mono>{upstream.name}</Mono>,
            })}
            <ChangedBadge id={upstream.id} ctx={ctx} />
          </span>
        }
        description={
          upstream.used_by.length > 0
            ? t("domains.siteStructure.usedBy", {
                count: upstream.used_by.length,
              })
            : t("domains.siteStructure.unused")
        }
      >
        <div className="flex min-w-0 flex-col gap-3">
          <Notes lines={upstream.comments} />
          {upstream.source ? (
            <p className="text-12 text-fg-muted">
              {t.rich("domains.siteStructure.fromInclude", {
                path: <Mono>{upstream.source}</Mono>,
              })}
            </p>
          ) : null}
          <RawDirectives directives={upstream.directives} parent={readOnly ? null : upstream.id} ctx={ctx} readOnly={readOnly} />
        </div>
      </Card>
    </div>
  );
}

const MODIFIERS = ["", "=", "^~", "~", "~*"] as const;
type Modifier = (typeof MODIFIERS)[number];

function useMatchOptions() {
  const t = useT();
  return MODIFIERS.map((modifier) => ({
    value: modifier === "" ? "prefix" : modifier,
    label: `${modifier === "" ? "" : `${modifier} · `}${t(`domains.siteStructure.match.${matchKind({ modifier, path: "/" })}`)}`,
  }));
}

function sameHead(server: SiteServer, modifier: string, path: string): boolean {
  const search = (locations: readonly SiteLocation[]): boolean =>
    locations.some((location) => (location.modifier === modifier && location.path === path) || search(location.locations));
  return search(server.locations);
}

function AddLocationDialog({ server, ctx, onClose }: { server: SiteServer; ctx: Ctx; onClose: () => void }) {
  const t = useT();
  const matchOptions = useMatchOptions();
  const nginx = ctx.kind === "nginx";
  const upstreams = ctx.structure.upstreams;
  const [template, setTemplate] = useState<LocationTemplate>("proxy");
  const [modifier, setModifier] = useState<Modifier>("");
  const [path, setPath] = useState("");
  const [to, setTo] = useState(upstreams[0]?.name ?? "");
  const [code, setCode] = useState<"301" | "302">("301");
  const [errors, setErrors] = useState<{ path?: string; to?: string }>({});
  const formId = useId();
  const serverName = server.names[0] ?? "_";

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const next: { path?: string; to?: string } = {};
    if (path.trim() === "") next.path = t("domains.siteStructure.pathRequired");
    else if (sameHead(server, nginx ? modifier : "", path.trim())) next.path = t("domains.siteStructure.samePath");
    if (to.trim() === "") next.to = t("domains.siteStructure.toRequired");
    setErrors(next);
    if (next.path !== undefined || next.to !== undefined) return;
    const draft: NewLocation = {
      server: server.id,
      modifier: nginx ? modifier : "",
      path: path.trim(),
      template,
      to: to.trim(),
      ...(template === "redirect" ? { code: Number(code) } : {}),
    };
    ctx.onEdit([addLocationOp(ctx.kind, draft, upstreams)]);
    onClose();
  };

  const templates = (nginx ? (["proxy", "static", "redirect"] as const) : (["proxy", "redirect"] as const)).map((value) => ({
    value,
    label: t(`domains.siteStructure.templates.${value}`),
    description: t(`domains.siteStructure.templates.${value}Description`),
  }));

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={t.rich("domains.siteStructure.addTitle", {
        server: <Mono>{serverName}</Mono>,
      })}
      description={t("domains.siteStructure.addDescription")}
      footer={
        <>
          <Button onClick={onClose}>{t("domains.cancel")}</Button>
          <Button variant="primary" type="submit" form={formId} icon={<Plus aria-hidden="true" />}>
            {t("domains.siteStructure.add")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <ChoiceCards<LocationTemplate>
          legend={t("domains.siteStructure.templateLegend")}
          options={templates}
          value={template}
          onValueChange={(next) => {
            setTemplate(next);
            setTo(next === "proxy" ? (upstreams[0]?.name ?? "") : "");
          }}
        />
        <div className="grid gap-4 sm:grid-cols-3">
          {nginx ? (
            <Field label={t("domains.siteStructure.matchField")} nativeLabel={false}>
              <Select value={modifier === "" ? "prefix" : modifier} onValueChange={(next) => setModifier(next === "prefix" ? "" : (next as Modifier))} options={matchOptions} />
            </Field>
          ) : null}
          <Field
            label={t("domains.siteStructure.pathField")}
            description={t("domains.siteStructure.pathHelp")}
            error={errors.path}
            className={nginx ? "sm:col-span-2" : "sm:col-span-3"}
          >
            <Input mono value={path} spellCheck={false} autoComplete="off" onChange={(event) => setPath(event.currentTarget.value)} />
          </Field>
        </div>
        {template === "proxy" ? (
          <Field label={t("domains.siteStructure.proxyToField")} description={t("domains.siteStructure.proxyToHelp")} error={errors.to}>
            <Input mono value={to} spellCheck={false} autoComplete="off" onChange={(event) => setTo(event.currentTarget.value)} />
          </Field>
        ) : template === "static" ? (
          <Field label={t("domains.siteStructure.directoryField")} description={t("domains.siteStructure.directoryHelp")} error={errors.to}>
            <Input mono value={to} spellCheck={false} autoComplete="off" onChange={(event) => setTo(event.currentTarget.value)} />
          </Field>
        ) : (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label={t("domains.siteStructure.redirectToField")} description={t("domains.siteStructure.redirectToHelp")} error={errors.to} className="sm:col-span-2">
              <Input mono value={to} spellCheck={false} autoComplete="off" onChange={(event) => setTo(event.currentTarget.value)} />
            </Field>
            <Field label={t("domains.siteStructure.codeField")} nativeLabel={false}>
              <Select<"301" | "302">
                value={code}
                onValueChange={setCode}
                options={[
                  { value: "301", label: t("domains.siteStructure.code301") },
                  { value: "302", label: t("domains.siteStructure.code302") },
                ]}
              />
            </Field>
          </div>
        )}
      </form>
    </Dialog>
  );
}

function DuplicateDialog({ location, server, ctx, onClose }: { location: SiteLocation; server: SiteServer; ctx: Ctx; onClose: () => void }) {
  const t = useT();
  const [path, setPath] = useState(location.path);
  const [error, setError] = useState<string | null>(null);
  const formId = useId();
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const next = path.trim();
    const problem = next === "" ? t("domains.siteStructure.pathRequired") : sameHead(server, location.modifier, next) ? t("domains.siteStructure.samePath") : null;
    setError(problem);
    if (problem !== null) return;
    ctx.onEdit([
      {
        op: "duplicate_block",
        target: location.id,
        args: ctx.kind === "nginx" ? locationArgs(location.modifier, next) : [next],
      },
    ]);
    onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="sm"
      title={t.rich("domains.siteStructure.duplicateTitle", {
        location: <Mono>{locationLabel(location)}</Mono>,
      })}
      description={t("domains.siteStructure.duplicateDescription")}
      footer={
        <>
          <Button onClick={onClose}>{t("domains.cancel")}</Button>
          <Button variant="primary" type="submit" form={formId} icon={<Copy aria-hidden="true" />}>
            {t("domains.siteStructure.duplicateAction")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate>
        <Field label={t("domains.siteStructure.pathField")} error={error}>
          <Input mono value={path} spellCheck={false} autoComplete="off" onChange={(event) => setPath(event.currentTarget.value)} />
        </Field>
      </form>
    </Dialog>
  );
}
