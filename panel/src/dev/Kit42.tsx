import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useMemo } from "react";
import type { ReactNode } from "react";

import { authKeys } from "../api/queries/auth";
import { PageHeader } from "../app/PageHeader";
import { Can, CommandHint, GuardedButton, KeyValueList, ReadOnlyLine, RowMenu, SurfaceSection } from "../components/page";
import { SectionSwitch } from "../components/ui/SectionSwitch";
import { Button, DataTable, FirstUse, HelpPopover, MenuItem, Mono, NameWithTags, ResourceCard, StatusPill } from "../components/ui";
import type { Column } from "../components/ui";
import { LogoTile } from "../components/ui/LogoTile";
import { OBJECT_ICONS, VERB_ICONS } from "../components/ui/objectIcons";
import { ICONS } from "../components/ui/icons";
import { BothThemes, Item, Section, Stage } from "./gallery";

/** What a role holds, for a specimen: the gallery's own session, so the reader's page can be drawn beside the admin's. */
function AsRole({ named, permissions, children }: { named: string; permissions: readonly string[]; children: ReactNode }) {
  const client = useMemo(() => {
    const seeded = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    seeded.setQueryData(authKeys.session, { authenticated: true, role: named, permissions: [...permissions] });
    return seeded;
  }, [named, permissions]);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

interface Account {
  name: string;
  registry: string;
  apps: readonly string[];
  state: "running" | "failed";
  said: string;
}

const ACCOUNTS: readonly Account[] = [
  { name: "ghcr.io", registry: "example-bot", apps: ["shop.example.com", "api.example.com", "queue-worker"], state: "running", said: "Fine, 2 h ago" },
  { name: "registry.example.com", registry: "deploy", apps: ["panel.example.net"], state: "failed", said: "Refused: password expired" },
  { name: "docker.io", registry: "example", apps: [], state: "running", said: "Fine, yesterday" },
];

function accountColumns(): readonly Column<Account>[] {
  return [
    { id: "name", header: "Registry", cell: (account) => <NameWithTags name={account.name} mono tags={account.apps} shown={1} title={`Apps of ${account.name}`} /> },
    { id: "user", header: "Account", cell: (account) => <Mono>{account.registry}</Mono> },
    {
      id: "state",
      header: "Last check",
      cell: (account) => <StatusPill state={account.state} label={account.said} appearance="inline" size="sm" />,
    },
  ];
}

function Surface() {
  return (
    <Section
      id="k42-surface"
      title="Section with a surface"
      description="A section that is a slab: its head row says what it is (the object's icon, a title, one line, its own action and, when the rule is long, a ?) and its content sits inside. It replaces a heading over loose paragraphs, which read with the same weight as everything around them."
    >
      <BothThemes stacked>
        <div className="flex flex-col gap-4">
          <SurfaceSection
            icon={OBJECT_ICONS.registry}
            title="Accounts"
            description="Each app uses the ones assigned to it, when it pulls and when it builds."
            help="Passwords and tokens are kept on this server and never shown again. To change one, type it again."
            actions={
              <Button size="sm" icon={<ICONS.add aria-hidden="true" />}>
                Add an account
              </Button>
            }
            flush
          >
            <DataTable caption="Registry accounts" columns={accountColumns()} rows={ACCOUNTS} getRowId={(account) => account.name} />
          </SurfaceSection>
          <SurfaceSection icon={OBJECT_ICONS.webhook} title="Address for webhooks" description="Where GitHub, GitLab, Gitea and Bitbucket tell this server about each push." actions={<a href="#k42-surface" className="text-13 font-medium text-accent-fg">Change it</a>}>
            <KeyValueList items={[{ label: "Address", value: "https://hooks.example.com/hooks", pasted: true }]} />
          </SurfaceSection>
          <SurfaceSection icon={OBJECT_ICONS.integration} title="Gitea, Forgejo and Bitbucket" description="They need no connecting here: each app has its own webhook." actions={<a href="#k42-surface" className="text-13 font-medium text-accent-fg">See an app&apos;s</a>} />
        </div>
      </BothThemes>
    </Section>
  );
}

function First() {
  return (
    <Section id="k42-first-use" title="First use" description="The first time of a thing, inside a section: a drawing, one sentence that says what it gives you and the button that starts it. Never a muted line with the button somewhere else.">
      <BothThemes stacked>
        <div className="flex flex-col gap-4">
          <FirstUse action={<Button variant="primary" icon={<ICONS.add aria-hidden="true" />}>Add an account</Button>}>
            Add the token of Hetzner Cloud, DigitalOcean or Vultr and you can create servers without leaving here.
          </FirstUse>
          <FirstUse>Only private images need an account. Public ones are pulled without it.</FirstUse>
        </div>
      </BothThemes>
    </Section>
  );
}

function Pairs() {
  return (
    <Section id="k42-pairs" title="Key-value pairs" description="The facts of one thing: the name on the left, the value on the right. A value that is pasted somewhere else wears its copy button always and wraps, so what is copied is what is seen.">
      <BothThemes>
        <KeyValueList
          items={[
            { label: "Address", value: "https://hooks.example.com/hooks", pasted: true },
            { label: "Owner", value: "acme", mono: false, copy: false },
            { label: "Receives pushes", value: "Yes, the last 2 h ago", mono: false, copy: false, hint: "From shop.example.com, branch main." },
          ]}
        />
      </BothThemes>
    </Section>
  );
}

function Cards() {
  return (
    <Section id="k42-card" title="Resource card" description="A thing that exists: its icon or its logo, its name, its state, two or three facts and what can be done with it. For the few that are the point of a section. A long list is a table.">
      <BothThemes>
        <div className="flex flex-col gap-4">
          <ResourceCard
            name="tienda_prod"
            untranslated
            logo={<LogoTile logo="postgresql" />}
            status={<StatusPill state="running" label="Running" size="sm" />}
            facts={[
              { label: "Size", value: "2.4 GB" },
              { label: "Last backup", value: "2 h ago" },
              { label: "Used by", value: "shop.example.com" },
            ]}
            actions={
              <RowMenu name="tienda_prod">
                <MenuItem icon={<VERB_ICONS.refresh />}>Back up now</MenuItem>
              </RowMenu>
            }
          />
          <ResourceCard
            name="production"
            untranslated
            icon={OBJECT_ICONS.cloud}
            status={<StatusPill state="failed" label="Token refused" size="sm" />}
            facts={[
              { label: "Provider", value: "Hetzner Cloud" },
              { label: "Servers", value: "2" },
            ]}
            actions={<Button size="sm">Change the token</Button>}
          />
        </div>
      </BothThemes>
    </Section>
  );
}

function Tags() {
  return (
    <Section id="k42-tags" title="Tags in a table" description="A name and its tags on one line: the first ones as pebbles after the name, the rest behind +N in a popover. Every row has the same height, whatever its tags; tags are never stacked under the name.">
      <Stage flush>
        <DataTable caption="Registry accounts with their apps as tags" columns={accountColumns()} rows={ACCOUNTS} getRowId={(account) => account.name} />
      </Stage>
      <TagSpecimens />
    </Section>
  );
}

function TagSpecimens() {
  return (
    <div className="flex flex-wrap items-center gap-x-10 gap-y-4">
      <Item label="no tags">
        <NameWithTags name="sessions" mono tags={[]} />
      </Item>
      <Item label="all of them fit">
        <NameWithTags name="blog_wp" mono tags={["web"]} />
      </Item>
      <Item label="the rest behind +N">
        <NameWithTags name="tienda_prod" mono tags={["production", "shop", "clients", "payments"]} />
      </Item>
    </div>
  );
}

function Help() {
  return (
    <Section id="k42-help" title="Help ?" description="The long rule is read when it is asked for: a real button, so a finger and a keyboard open it and not only a pointer resting on it. The page shows the state; this is the explanation.">
      <Stage>
        <div className="flex items-center gap-2 text-14 text-fg">
          Trusted code hosts
          <HelpPopover label="About trusted code hosts" title="Trusted code hosts">
            <p className="text-13 text-fg-muted">So nobody can pose as GitHub or your Gitea between this server and the real one. If a code host really changed its key, forget the old one and the new one is kept on the next connection.</p>
          </HelpPopover>
        </div>
      </Stage>
    </Section>
  );
}

function Roles() {
  const reader = ["apps.read"] as const;
  const admin = ["apps.read", "apps.manage"] as const;
  const page = (
    <div className="flex flex-col gap-4">
      <ReadOnlyLine permission="apps.manage" />
      <div className="flex flex-wrap items-center gap-3">
        <GuardedButton permission="apps.manage" variant="primary" icon={<ICONS.add aria-hidden="true" />}>
          Add an account
        </GuardedButton>
      </div>
      <SurfaceSection title="Accounts" description="Each row's actions are drawn only for who can use them." flush>
        <ul aria-label="Accounts" className="divide-y divide-border">
          {["ghcr.io", "docker.io"].map((name) => (
            <li key={name} className="flex h-12 items-center justify-between px-5 text-13">
              <Mono>{name}</Mono>
              <Can permission="apps.manage">
                <RowMenu name={name}>
                  <MenuItem icon={<VERB_ICONS.refresh />}>Check</MenuItem>
                </RowMenu>
              </Can>
            </li>
          ))}
        </ul>
      </SurfaceSection>
    </div>
  );
  return (
    <Section
      id="k42-roles"
      title="What each role can do"
      description="An action declares the permission it takes. The page's own primary action stays, disabled, with a lock and the reason one press away, because it tells a reader that the thing exists and who can do it. The actions of each row are not drawn, because a disabled button repeated on every row is noise. One line at the top says once that the page can only be read."
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <div data-theme="light" className="min-w-0 rounded-card border border-border bg-bg p-5 text-fg">
          <p className="mb-4 text-12 text-fg-faint">Administration</p>
          <AsRole named="admin" permissions={admin}>
            {page}
          </AsRole>
        </div>
        <div data-theme="light" className="min-w-0 rounded-card border border-border bg-bg p-5 text-fg">
          <p className="mb-4 text-12 text-fg-faint">Reader</p>
          <AsRole named="viewer" permissions={reader}>
            {page}
          </AsRole>
        </div>
      </div>
    </Section>
  );
}

function Hints() {
  return (
    <Section id="k42-hints" title="Command with its reason" description="A command is shown only for a reason: the console cannot do it (terminal only), it is for another server, or the system's own output names it. A command that repeats a button is noise, and a command without a reason is on the way out.">
      <Stage>
        <div className="flex flex-col gap-3">
          <CommandHint command="noust central seal" reason="solo-terminal" />
          <CommandHint command="noust fleet authorize --access deploy" reason="otro-servidor" />
          <CommandHint command="nginx -t" reason="salida-del-sistema" />
        </div>
      </Stage>
    </Section>
  );
}

function HeaderMeasure() {
  return (
    <Section
      id="k42-header"
      title="A header as wide as its content"
      description="The title and its actions end where the content does, never at the edge of the window. The master switch of what the page governs is on the title's line, right after it: it belongs to the thing it turns on and off."
    >
      <Stage>
        <div className="max-w-settings" data-gallery-specimen="header-measure">
          <PageHeader
            title="Channels"
            master={<SectionSwitch label="Send notifications" checked onCheckedChange={() => undefined} />}
            primaryAction={<Button variant="primary" icon={<ICONS.add aria-hidden="true" />}>Add a channel</Button>}
          />
          <div className="rounded-card border border-border bg-surface p-4 text-13 text-fg-muted">The content: 880px wide, as far as the header goes.</div>
        </div>
      </Stage>
    </Section>
  );
}

/** Components of 4.2: the content kit (design 3.6). Everything a page needs to say what each thing is before it is read. */
export function Kit42() {
  return (
    <>
      <Surface />
      <First />
      <Pairs />
      <Cards />
      <Tags />
      <Help />
      <Roles />
      <HeaderMeasure />
      <Hints />
    </>
  );
}
