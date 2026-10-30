import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  Checkbox,
  ChoiceCards,
  DataTable,
  EmptyState,
  FleetCourses,
  HelpPopover,
  IconButton,
  Illustration,
  Input,
  ImageLogoTile,
  LogoTile,
  Menu,
  MenuItem,
  MenuSeparator,
  Meter,
  Notice,
  Popover,
  Progress,
  SectionSwitch,
  Select,
  ServerText,
  SlidingIndicator,
  Skeleton,
  StatusPill,
  Switch,
  SwitchedRegion,
  Tooltip,
} from "../components/ui";
import type { Column, FleetCourse, LogoSlug, Status } from "../components/ui";
import { ICONS } from "../components/ui/icons";
import { LOGOS } from "../components/ui/logos/logoData";
import { OBJECT_ICONS, VERB_ICONS } from "../components/ui/objectIcons";
import { SegmentedControl } from "../components/page/SegmentedControl";
import { Tab, TabList, TabPanel, Tabs } from "../components/ui/Tabs";
import { cx } from "../lib/cx";
import { BothThemes, Item, Row, Section } from "./gallery";

/** The three heights, with a light from above: what the gallery of the mockups called "tres alturas, una luz". */
function Heights() {
  return (
    <BothThemes>
      <div className="flex flex-col gap-8">
        {(
          [
            ["Carved", "Where something is typed or chosen inside", "carved"],
            ["Seated", "What is read. It does not react", "seated"],
            ["Raised", "What is pressed: rises under the pointer, sinks when pressed", "raised"],
          ] as const
        ).map(([name, rule, kind]) => (
          <div key={kind} className="flex flex-col gap-3">
            <div>
              <p className="title text-18 text-fg">{name}</p>
              <p className="text-13 text-fg-muted">{rule}</p>
            </div>
            <div className="grid grid-cols-3 gap-4 max-sm:grid-cols-1">
              {(["Normal", "Over", "Pressed"] as const).map((state) => {
                const force = state === "Over" ? "hover" : state === "Pressed" ? "pressed" : undefined;
                const props = force !== undefined ? { "data-force": force } : {};
                return (
                  <div key={state} className="flex flex-col items-start gap-2">
                    {kind === "carved" ? (
                      <div
                        {...(state === "Pressed" ? { "data-force": "focus" } : {})}
                        className={cx(
                          "elev-carved flex h-control-md w-full items-center gap-2 rounded-control px-3 text-13 text-fg-faint",
                          state === "Over" ? "bg-bg-sunken-deep" : "bg-bg-sunken",
                        )}
                      >
                        <ICONS.search aria-hidden="true" className="size-icon-sm" />
                        Search
                      </div>
                    ) : kind === "seated" ? (
                      state === "Pressed" ? (
                        <p className="py-3 text-13 text-fg-faint">A panel is not pressed</p>
                      ) : (
                        <div className="elev-seated w-full rounded-card border border-border bg-surface px-3.5 py-2.5 text-13">
                          <p className="font-medium text-fg">Copy of 8 databases</p>
                          <p className="text-fg-muted">on datos-01, 4 hours ago</p>
                        </div>
                      )
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        <Button variant="primary" {...props}>
                          Deploy
                        </Button>
                        <Button {...props}>Copy now</Button>
                      </div>
                    )}
                    <span className="text-12 text-fg-faint">{state}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </BothThemes>
  );
}

const STATES = [
  ["Normal", {}],
  ["Over", { "data-force": "hover" }],
  ["Pressed", { "data-force": "pressed" }],
  ["Disabled", { disabled: true }],
  ["Focus", { "data-force": "focus" }],
] as const;

function Controls() {
  const [checked, setChecked] = useState(true);
  const [on, setOn] = useState(true);
  const [range, setRange] = useState<"1h" | "24h" | "7d">("24h");
  const [role, setRole] = useState<"viewer" | "operator" | "admin">("operator");
  const [engine, setEngine] = useState("postgres");
  return (
    <div className="flex flex-col gap-8">
      <BothThemes>
        <div className="flex flex-col gap-6">
          {(["primary", "secondary", "ghost", "danger"] as const).map((variant) => (
            <Row key={variant}>
              {STATES.map(([label, props]) => (
                <Item key={label} label={`${variant}, ${label.toLowerCase()}`}>
                  <Button variant={variant} icon={variant === "primary" ? <OBJECT_ICONS.deployment aria-hidden="true" /> : undefined} {...props}>
                    {variant === "danger" ? "Delete" : variant === "primary" ? "Deploy" : "Copy now"}
                  </Button>
                </Item>
              ))}
              <Item label={`${variant}, loading`}>
                <Button variant={variant} loading>
                  Working
                </Button>
              </Item>
            </Row>
          ))}
          <Row>
            <Item label="small">
              <Button size="sm">Restart</Button>
            </Item>
            <Item label="large">
              <Button size="lg" variant="primary">
                Sign in
              </Button>
            </Item>
            <Item label="icon button, raised">
              <IconButton variant="secondary" label="Change theme" icon={<ICONS.search />} />
            </Item>
            <Item label="icon button, flat in a row">
              <IconButton label="More actions" icon={<ICONS.more />} />
            </Item>
            <Item label="icon button, held down">
              <IconButton label="Wrap lines" pressed icon={<ICONS.copy />} />
            </Item>
          </Row>
        </div>
      </BothThemes>
      <BothThemes>
        <div className="flex max-w-96 flex-col gap-5">
          <Item label="field, normal">
            <Input aria-label="Name" placeholder="Search" icon={<ICONS.search />} />
          </Item>
          <Item label="field, over">
            <Input aria-label="Name" placeholder="Search" className="bg-bg-sunken-deep" />
          </Item>
          <Item label="field, focus">
            <Input aria-label="Name" defaultValue="web-01" data-force="focus" />
          </Item>
          <Item label="field, invalid">
            <Input aria-label="Name" defaultValue="web 01" aria-invalid />
          </Item>
          <Item label="field, disabled">
            <Input aria-label="Name" defaultValue="web-01" disabled />
          </Item>
          <Item label="select">
            <Select
              label="Engine"
              value={engine}
              onValueChange={setEngine}
              options={[
                { value: "postgres", label: "PostgreSQL" },
                { value: "mysql", label: "MySQL" },
                { value: "redis", label: "Redis" },
              ]}
            />
          </Item>
          <div className="flex flex-wrap gap-6">
            <Item label="checkbox">
              <Checkbox label="Back up before updating" checked={checked} onCheckedChange={setChecked} />
            </Item>
            <Item label="switch, on">
              <Switch aria-label="Notifications" checked={on} onCheckedChange={setOn} />
            </Item>
            <Item label="switch, off">
              <Switch aria-label="Previews" checked={false} onCheckedChange={() => undefined} />
            </Item>
            <Item label="switch, busy">
              <Switch aria-label="Backups" checked busy onCheckedChange={() => undefined} />
            </Item>
            <Item label="switch, disabled">
              <Switch aria-label="Locked" checked={false} disabled />
            </Item>
          </div>
          <Item label="segmented control">
            <SegmentedControl
              label="Time range"
              value={range}
              onValueChange={setRange}
              options={[
                { value: "1h", label: "1 h" },
                { value: "24h", label: "24 h" },
                { value: "7d", label: "7 d" },
              ]}
            />
          </Item>
          <Item label="choice cards, with the detail folded">
            <ChoiceCards
              legend="What they can do"
              columns={1}
              value={role}
              onValueChange={setRole}
              options={[
                { value: "viewer", label: "Viewer", description: "Sees everything and changes nothing.", icon: OBJECT_ICONS.overview },
                {
                  value: "operator",
                  label: "Operator",
                  description: "Deploys, restarts and reads the logs.",
                  icon: OBJECT_ICONS.deployment,
                  detail: "Cannot change who has access, the server's settings or the fleet. Everything an operator does is in the audit trail.",
                },
                { value: "admin", label: "Administrator", description: "Everything, including people and settings.", icon: OBJECT_ICONS.settings },
              ]}
            />
          </Item>
        </div>
      </BothThemes>
    </div>
  );
}

const ROWS = [
  { id: "1", name: "tienda.example.com", server: "web-01", state: "running" as Status },
  { id: "2", name: "api.example.net", server: "web-02", state: "failed" as Status },
  { id: "3", name: "revista.example.org", server: "clientes-01", state: "deploying" as Status },
  { id: "4", name: "demo.example.com", server: "clientes-01", state: "stopped" as Status },
];

const COLUMNS: Column<(typeof ROWS)[number]>[] = [
  { id: "name", header: "Application", cell: (row) => <span className="font-medium">{row.name}</span> },
  { id: "server", header: "Server", cell: (row) => row.server },
  { id: "state", header: "State", cell: (row) => <StatusPill state={row.state} appearance="inline" size="sm" /> },
];

const FLEET: FleetCourse[] = [
  {
    id: "web-01",
    name: "web-01",
    place: "Madrid",
    diskPercent: 52,
    apps: [
      { id: "a", name: "pruebas.com", state: "stopped", stateLabel: "Stopped" },
      ...Array.from({ length: 9 }, (_, index) => ({ id: `w${String(index)}`, name: `app-${String(index)}.net`, state: "running" as const, stateLabel: "Running" })),
    ],
  },
  {
    id: "web-02",
    name: "web-02",
    place: "Frankfurt",
    diskPercent: 58,
    apps: [
      { id: "b", name: "api.net", state: "failed", stateLabel: "Failed" },
      ...["portal", "metricas", "flujos", "git", "docs", "tienda", "almacen"].map((name) => ({ id: name, name: `${name}.net`, state: "running" as const, stateLabel: "Running" })),
    ],
  },
  {
    id: "clientes-01",
    name: "clientes-01",
    place: "Helsinki",
    diskPercent: 91,
    apps: [
      { id: "c", name: "revista.org", state: "deploying", stateLabel: "Deploying" },
      { id: "d", name: "demo.org", state: "stopped", stateLabel: "Stopped" },
      ...["wiki", "fotos", "papeles"].map((name) => ({ id: name, name: `${name}.org`, state: "running" as const, stateLabel: "Running" })),
    ],
  },
  { id: "web-03", name: "web-03", place: "Singapore", diskPercent: 7, apps: [] },
  {
    id: "datos-01",
    name: "datos-01",
    place: "Ashburn",
    problem: { state: "failed", label: "Not answering" },
    apps: ["radio", "tienda"].map((name) => ({ id: name, name: `${name}.org`, state: "running" as const, stateLabel: "Running" })),
  },
];

function Surfaces() {
  const [open, setOn] = useState(false);
  const [section, setSection] = useState(true);
  return (
    <div className="flex flex-col gap-8">
      <BothThemes>
        <div className="flex flex-col gap-8">
          <Row>
            <Item label="status: well stays stone, a problem takes its ground">
              <div className="flex flex-wrap items-center gap-3">
                {(["running", "static", "deploying", "queued", "warning", "failed", "stopped", "unknown"] as const).map((state) => (
                  <StatusPill key={state} state={state} />
                ))}
              </div>
            </Item>
          </Row>
          <Row>
            <Item label="status, small, in the head of a card">
              <div className="flex flex-wrap items-center gap-3">
                <StatusPill state="running" size="sm" label="On" />
                <StatusPill state="stopped" size="sm" label="Off" />
                <StatusPill state="warning" size="sm" label="On, no channel" />
              </div>
            </Item>
            <Item label="badges: neutral, and accent only when selected">
              <div className="flex gap-2">
                <Badge>Default</Badge>
                <Badge mono>v20.11</Badge>
                <Badge tone="accent">Selected</Badge>
              </div>
            </Item>
          </Row>
          <DataTable caption="Applications" rows={ROWS} columns={COLUMNS} getRowId={(row) => row.id} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Card title="Notifications" status={<StatusPill state="running" size="sm" label="On" />} description="Where Noust tells you something happened.">
              <Meter label="Disk" value={52} />
              <div className="mt-3">
                <Meter label="Disk" value={91} />
              </div>
              <div className="mt-3">
                <Progress label="Restoring" value={64} />
              </div>
            </Card>
            <Card title="Loading" loading>
              <div className="flex flex-col gap-2">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-control-md w-full" />
              </div>
            </Card>
            <Card tone="fail" padding="sm" as="div">
              <p className="title text-14 text-fg">api.net</p>
              <p className="text-13 text-fg">The service has failed</p>
            </Card>
            <Card tone="warn" padding="sm" as="div">
              <p className="title text-14 text-fg">portal.net</p>
              <p className="text-13 text-fg">The certificate expires in 6 days</p>
            </Card>
          </div>
          <Item label="a sentence of the server in the console's language (ServerText), with its fix, in a notice">
            <Notice
              tone="warning"
              title={
                <ServerText
                  coded={{ code: "terminal.server_off", params: { command: "noust config set terminal.server.enabled true" } }}
                  text="The terminal on the server itself is off (terminal.server.enabled)"
                />
              }
            >
              <ServerText
                coded={{ code: "terminal.server_off", params: { command: "noust config set terminal.server.enabled true" } }}
                text="It is a root shell, so it stays off until an administrator turns it on: noust config set terminal.server.enabled true"
                part="hint"
              />
            </Notice>
          </Item>
          <Row>
            <Menu trigger={<Button variant="secondary">Open a menu</Button>} open={open} onOpenChange={setOn}>
              <MenuItem icon={<OBJECT_ICONS.deployment />}>Deploy again</MenuItem>
              <MenuItem icon={<VERB_ICONS.refresh />}>Restart</MenuItem>
              <MenuItem icon={<OBJECT_ICONS.log />}>See the logs</MenuItem>
              <MenuSeparator />
              <MenuItem destructive icon={<ICONS.delete />}>
                Delete application
              </MenuItem>
            </Menu>
            <Popover trigger={<Button variant="secondary">Open a popover</Button>} title="Deploy keys" description="A key that lets this server read one private repository." />
            <div className="flex items-center gap-1.5 text-13 text-fg">
              Deploy keys
              <HelpPopover label="About deploy keys" title="Deploy keys">
                A key that lets this server read one private repository and nothing else. It is made when you add the repository.
              </HelpPopover>
            </div>
            <Tooltip content="Names an icon, and nothing else">
              <IconButton label="Names an icon" icon={<ICONS.info />} tooltip={false} />
            </Tooltip>
          </Row>
        </div>
      </BothThemes>

      <BothThemes>
        <div className="flex flex-col gap-6">
          <EmptyState
            variant="firstUse"
            illustration="first-steps"
            illustrationLabel="The noust with a boat in it"
            title="web-03 has no applications yet"
            description="Deploy the first from a repository, an image or a recipe."
            action={
              <>
                <Button variant="primary" icon={<OBJECT_ICONS.deployment aria-hidden="true" />}>
                  Deploy an application
                </Button>
                <Button icon={<OBJECT_ICONS.recipe aria-hidden="true" />}>See the catalog</Button>
              </>
            }
          />
          <EmptyState variant="firstUse" illustration="empty-slab" title="No backups yet" description="Back up your first database." />
          <EmptyState variant="inline" title="Nothing matched your filter" />
        </div>
      </BothThemes>

      <BothThemes>
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="title text-18 text-fg">Alerts</p>
            <SectionSwitch label="Alerts" checked={section} onCheckedChange={setSection} />
          </div>
          <SwitchedRegion on={section}>
            <Card title="Rules" description="What raises an alert.">
              <p className="text-13 text-fg-muted">The list looks off when the section is off; it can still be read.</p>
            </Card>
          </SwitchedRegion>
        </div>
      </BothThemes>
    </div>
  );
}

function Logos() {
  const slugs = Object.keys(LOGOS) as LogoSlug[];
  return (
    <BothThemes>
      <div className="flex flex-col gap-5">
        <Row>
          {(["sm", "md", "lg"] as const).map((size) => (
            <Item key={size} label={size}>
              <LogoTile logo="nextcloud" size={size} />
            </Item>
          ))}
        </Row>
        <Row>
          <Item label="a served logo (a recipe's), in the same tile">
            <ImageLogoTile src="/favicon.svg" fallback="NO" size="lg" />
          </Item>
          <Item label="no logo, or it did not load: two letters, the same box">
            <ImageLogoTile src={null} fallback="OW" size="lg" />
          </Item>
        </Row>
        <ul className="grid grid-cols-3 gap-x-4 gap-y-3 sm:grid-cols-4">
          {slugs.map((slug) => (
            <li key={slug} className="flex min-w-0 items-center gap-2.5 text-13">
              <LogoTile logo={slug} size="sm" />
              <span className="truncate">{LOGOS[slug].title}</span>
            </li>
          ))}
        </ul>
      </div>
    </BothThemes>
  );
}

const NAV_ITEMS = [
  { id: "home", label: "Home", Icon: OBJECT_ICONS.overview, group: 0 },
  { id: "apps", label: "Applications", Icon: OBJECT_ICONS.app, group: 0 },
  { id: "databases", label: "Databases", Icon: OBJECT_ICONS.database, group: 0 },
  { id: "schedules", label: "Scheduled tasks", Icon: OBJECT_ICONS.schedule, group: 0 },
  { id: "domains", label: "Domains", Icon: OBJECT_ICONS.domain, group: 1 },
  { id: "logs", label: "Logs", Icon: OBJECT_ICONS.log, group: 1 },
  { id: "alerts", label: "Alerts", Icon: OBJECT_ICONS.alert, group: 1 },
  { id: "activity", label: "Activity", Icon: OBJECT_ICONS.activity, group: 1 },
] as const;

/**
 * A vertical navigation list of eight items in two groups, with its own sliding indicator. This is how
 * the frame mounts the piece on a bar: the list is `relative isolate`, the indicator is its first child,
 * each item is `relative` and the current one is marked `aria-current="page"` and has no ground of its
 * own. The same list folded to icons shows the indicator following the width.
 */
export function NavDemo() {
  const [current, setCurrent] = useState<string | null>("apps");
  const [folded, setFolded] = useState(false);
  return (
    <div className="flex flex-wrap items-start gap-8">
      <nav
        aria-label="Navigation demo"
        data-testid="nav-demo"
        className={cx("relative isolate flex flex-col gap-0.5 rounded-wall bg-bg-rail p-3", folded ? "w-16" : "w-60")}
      >
        <SlidingIndicator className="rounded-control bg-surface" />
        {NAV_ITEMS.map((item, index) => (
          <div key={item.id} className={cx(index > 0 && NAV_ITEMS[index - 1]?.group !== item.group && "mt-4")}>
            <button
              type="button"
              data-testid={`nav-${item.id}`}
              {...(current === item.id ? { "aria-current": "page" as const } : {})}
              aria-label={folded ? item.label : undefined}
              onClick={() => setCurrent(item.id)}
              className={cx(
                "elev-lift relative flex h-10 w-full cursor-pointer items-center gap-3 rounded-control px-3 text-14 font-medium outline-none",
                "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
                folded && "justify-center px-0",
                current === item.id ? "text-fg" : "text-fg-muted hover:bg-rail-hover hover:text-fg",
              )}
            >
              <item.Icon aria-hidden="true" className={cx("size-icon-md shrink-0", current === item.id && "text-accent-fg")} />
              {folded ? null : item.label}
            </button>
          </div>
        ))}
      </nav>
      <div className="flex flex-col items-start gap-3">
        <p className="max-w-64 text-13 text-fg-muted">
          Click an item, or arrow through them with the keyboard. The piece slides to the chosen one, also between the two groups. It does not slide
          when the bar folds, when the page loads or when there is no chosen item.
        </p>
        <Button size="sm" onClick={() => setFolded((value) => !value)} data-testid="nav-fold">
          {folded ? "Unfold the bar" : "Fold the bar to icons"}
        </Button>
        <Button size="sm" onClick={() => setCurrent(null)} data-testid="nav-none">
          A screen with no entry
        </Button>
      </div>
    </div>
  );
}

function Motion() {
  const [range, setRange] = useState<"1h" | "6h" | "24h" | "7d" | "30d">("6h");
  return (
    <div className="flex flex-col gap-8">
      <BothThemes>
        <div className="flex flex-col gap-8">
          <Item label="tabs: the raised piece moves and changes width to the chosen one (200ms, the standard curve)">
            <Tabs defaultValue="overview">
              <TabList aria-label="Application sections">
                <Tab value="overview">Overview</Tab>
                <Tab value="deployments" count={12}>
                  Deployments
                </Tab>
                <Tab value="logs">Logs</Tab>
                <Tab value="environment">Environment variables</Tab>
              </TabList>
              <TabPanel value="overview">The overview.</TabPanel>
              <TabPanel value="deployments">Twelve deployments.</TabPanel>
              <TabPanel value="logs">The logs.</TabPanel>
              <TabPanel value="environment">The variables.</TabPanel>
            </Tabs>
          </Item>
          <Item label="segments: the same piece">
            <SegmentedControl
              label="Time range"
              value={range}
              onValueChange={setRange}
              options={[
                { value: "1h", label: "1 h" },
                { value: "6h", label: "6 h" },
                { value: "24h", label: "24 h" },
                { value: "7d", label: "7 d" },
                { value: "30d", label: "30 d" },
              ]}
            />
          </Item>
        </div>
      </BothThemes>
      <BothThemes>
        <NavDemo />
      </BothThemes>
    </div>
  );
}

function Illustrations() {
  return (
    <BothThemes>
      <div className="flex flex-wrap items-end gap-10">
        <Item label="first steps: the noust, with a hull in it">
          <Illustration name="first-steps" className="max-w-full" />
        </Item>
        <Item label="empty slab">
          <Illustration name="empty-slab" />
        </Item>
      </div>
    </BothThemes>
  );
}

function Fleet() {
  return (
    <BothThemes stacked>
      <FleetCourses label="Servers and their applications" courses={FLEET} />
    </BothThemes>
  );
}

function Typefaces() {
  return (
    <BothThemes>
      <div className="flex flex-col gap-4">
        <p className="display text-32 text-fg">Inicio, Aplicaciones, web-01</p>
        <p className="title text-18 text-fg">The section title: Bricolage Grotesque in its narrow widths</p>
        <p className="text-14 text-fg">The interface is Atkinson Hyperlegible Next, drawn so that characters cannot be mistaken for one another: 0 O 1 l I.</p>
        <p className="mono text-13 text-fg-muted">noust deploy shop.example.com --branch main  # JetBrains Mono, system values only</p>
      </div>
    </BothThemes>
  );
}

/** The identity of 4.1, dry stone: the three heights, the pieces that carry them and the piece that moves. */
export function Stone() {
  return (
    <>
      <Section
        id="stone-heights"
        title="Three heights, one light"
        description="The light falls from above: carved sinks in, seated rests, raised is pressed. Hard edges only, a tone, a light edge on top and a darker joint beneath, never a blurred shadow repeated under everything."
      >
        <Heights />
      </Section>
      <Section
        id="stone-type"
        title="Typefaces"
        description="Atkinson Hyperlegible Next for the interface, Bricolage Grotesque in its narrow widths for titles, JetBrains Mono for what the system says and nothing else."
      >
        <Typefaces />
      </Section>
      <Section
        id="stone-controls"
        title="Controls, in their states"
        description="Normal, over, pressed, disabled, focus and loading. What is typed or chosen inside is carved; what is pressed is raised and has the edge of its own colour when it is the primary or the danger."
      >
        <Controls />
      </Section>
      <Section
        id="stone-surfaces"
        title="Surfaces, state and data"
        description="Panels are seated and flat inside. Only what is not well tints its surface: a healthy thing stays stone."
      >
        <Surfaces />
      </Section>
      <Section
        id="stone-motion"
        title="The piece that moves"
        description="The raised piece of the chosen item slides to the one the person picks, horizontally in tabs and segments and vertically in a navigation list, in 200ms and by the standard curve; nothing else here is animated for decoration."
      >
        <Motion />
      </Section>
      <Section
        id="stone-fleet"
        title="The fleet in courses"
        description="One course per server, one slab per application, in a regular grid; the problems first, the rest as +N running."
      >
        <Fleet />
      </Section>
      <Section
        id="stone-logos"
        title="Real logos, and the first time"
        description="A tile is only for a real logo. An illustration is only for the first time of a main function."
      >
        <Logos />
        <Illustrations />
      </Section>
    </>
  );
}
