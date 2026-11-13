import { ObjectMenu, OwnerMark, VersionMark } from "../components/page";
import type { ObjectAction, VersionKind } from "../components/page";
import { DataTable, DiffLines, Mono, StatusPill } from "../components/ui";
import type { Column, Status } from "../components/ui";
import { ICONS } from "../components/ui/icons";
import { OBJECT_ICONS, VERB_ICONS } from "../components/ui/objectIcons";
import { BothThemes, DoDont, Item, Row, Section } from "./gallery";

interface Sample {
  name: string;
  state: Status;
  stateWord: string;
  owner: { kind: "app" | "database" | "noust" | "nobody"; subject: string };
}

const SAMPLES: readonly Sample[] = [
  { name: "shop-example-com-web-1", state: "running", stateWord: "Running", owner: { kind: "app", subject: "shop.example.com" } },
  { name: "noust-postgresql-pg16", state: "running", stateWord: "Running", owner: { kind: "database", subject: "postgresql@pg16" } },
  { name: "noust-buildkit", state: "running", stateWord: "Running", owner: { kind: "noust", subject: "buildkit" } },
  { name: "adminer", state: "stopped", stateWord: "Stopped", owner: { kind: "nobody", subject: "" } },
];

const noop = (): void => undefined;

const OWN: readonly ObjectAction[] = [
  { id: "logs", label: "See logs", icon: <OBJECT_ICONS.log />, onSelect: noop },
  { id: "console", label: "Open console", icon: <OBJECT_ICONS.terminal />, onSelect: noop },
  { id: "stop", label: "Stop", icon: <VERB_ICONS.turnOff />, onSelect: noop },
  { id: "remove", label: "Remove…", icon: <ICONS.delete />, onSelect: noop, destructive: true },
];

const COLUMNS: readonly Column<Sample>[] = [
  { id: "name", header: "Container", card: "title", cell: (row) => <Mono tone="default">{row.name}</Mono> },
  { id: "state", header: "State", card: "status", width: "w-36", cell: (row) => <StatusPill state={row.state} label={row.stateWord} appearance="inline" size="sm" /> },
  {
    id: "owner",
    header: "Owner",
    cell: (row) => (
      <OwnerMark kind={row.owner.kind} subject={row.owner.subject} {...(row.owner.kind === "app" || row.owner.kind === "database" ? { target: { to: "/__design" } } : {})} />
    ),
  },
];

const VERSIONS: readonly { kind: VersionKind; note: string }[] = [
  { kind: "current", note: "the latest the registry has" },
  { kind: "newer", note: "a newer version of the same major" },
  { kind: "major", note: "a new major is a decision" },
  { kind: "keeps", note: "an application keeps it to go back to" },
  { kind: "builtHere", note: "no registry to ask" },
  { kind: "untagged", note: "no name" },
  { kind: "notLooked", note: "looking is a button" },
  { kind: "unknown", note: "the registry did not answer" },
];

const DIFF = [
  { kind: "same", text: "  web:" },
  { kind: "same", text: "    image: shop-example-com-web:release-41" },
  { kind: "add", text: "    deploy:" },
  { kind: "add", text: "      resources:" },
  { kind: "add", text: "        limits:" },
  { kind: "add", text: "          memory: 768M" },
  { kind: "del", text: "    mem_limit: 512m" },
  { kind: "same", text: "    environment:" },
] as const;

/** Components of the Docker zone (item 189): whose each thing is, where its image stands, what its menu offers and a change shown as lines. */
export function KitDocker() {
  return (
    <>
      <Section
        id="kd-owner"
        title="Owner mark"
        description="Docker is Noust's own now: every container, image, volume, network and stack says whose it is, in the same place and the same way. An application's or a database engine's is a link to it; Noust's own and what nobody of Noust made are text, in words, never a dash. A name is not code, so it is in the interface typeface and not translated; a screen reader hears what kind of owner it is."
      >
        <BothThemes stacked>
          <div className="flex flex-col gap-6">
            <Item label="In a table: the icon of the kind, the name, a link where there is somewhere to go">
              <div className="w-full">
                <DataTable caption="Containers and their owners" columns={COLUMNS} rows={SAMPLES} getRowId={(row) => row.name} mobile="scroll" />
              </div>
            </Item>
            <Item label="As a sentence, under a title or in a dialog">
              <div className="flex flex-col gap-2">
                <OwnerMark variant="sentence" kind="app" subject="shop.example.com" target={{ to: "/__design" }} />
                <OwnerMark variant="sentence" kind="database" subject="postgresql@pg16" target={{ to: "/__design" }} />
                <OwnerMark variant="sentence" kind="noust" subject="buildkit" />
                <OwnerMark variant="sentence" kind="nobody" subject="" />
              </div>
            </Item>
          </div>
        </BothThemes>
        <DoDont
          rule="A thing of nobody's says so in words"
          doThis={<OwnerMark kind="nobody" subject="" />}
          notThis={<span className="text-13 text-fg-muted">-</span>}
          why="A dash says the owner is unknown or missing, and Noust knows exactly: it did not create it. The words are also what leads a person to bring a stack to Noust."
        />
      </Section>
      <Section
        id="kd-version"
        title="Version mark"
        description="Where an image stands against what its registry has, in the same place on the summary, the images list and an image's dialog. Only what asks for a look wears a state: a new version waits, a new major is a warning (a glyph and a word, never colour alone); an image that is well is its word in stone."
      >
        <BothThemes>
          <Row>
            {VERSIONS.map((entry) => (
              <Item key={entry.kind} label={entry.note}>
                <VersionMark kind={entry.kind} />
              </Item>
            ))}
          </Row>
        </BothThemes>
      </Section>
      <Section
        id="kd-menu"
        title="Object menu"
        description="The menu at the end of the row of an object that has an owner. It offers what works there and nothing else: a verb the owner takes is not made available and then refused. What the owner does is one line that leads to the owner, never a disabled copy of each verb on every row; an object nothing can be done to draws no menu, and what removes is last."
      >
        <BothThemes>
          <Row>
            <Item label="A container nobody of Noust made">
              <ObjectMenu name="adminer" actions={OWN} />
            </Item>
            <Item label="A container of an application: the rest is done from it">
              <ObjectMenu
                name="shop-example-com-web-1"
                actions={OWN.slice(0, 1)}
                viaOwner={{ label: "Change from shop.example.com", description: "Restarting, stopping and removing are done from the application", icon: <OBJECT_ICONS.app />, target: { to: "/__design" } }}
              />
            </Item>
            <Item label="Nothing to offer: no menu">
              <ObjectMenu name="noust-buildkit" actions={[]} />
              <span className="text-13 text-fg-muted">(nothing is drawn)</span>
            </Item>
          </Row>
        </BothThemes>
      </Section>
      <Section
        id="kd-diff"
        title="Diff lines"
        description="The lines a change adds and removes, the same drawing for a declaration's review and a Compose document's: a plus or a minus as well as a ground, and a word for a screen reader, so the change reads without its colour. The lines are system text, in mono, never cut."
      >
        <BothThemes stacked>
          <div className="w-full max-w-xl">
            <DiffLines lines={DIFF} label="Changes to web" labels={{ added: "Added line", removed: "Removed line" }} />
          </div>
        </BothThemes>
      </Section>
    </>
  );
}
