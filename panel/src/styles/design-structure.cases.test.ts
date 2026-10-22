/**
 * One example of each structural rule breaking and not breaking (styles/designStructure.ts). The
 * ratchet (design-structure.test.ts) says how many breaches the console has today; this says
 * what a breach is, so a rule that stops seeing its own pattern fails here and not silently in
 * the baseline. A new rule brings its example or the first test below fails.
 */

import { describe, expect, it } from "vitest";

import { NOTICE_LONG_WORDS, STRUCTURE_RULES, countStructure, countWords, messageLookup } from "./designStructure";
import type { StructureContext } from "./designStructure";

/** A catalog of a few sentences, for the rules that count words. */
const WORDS: StructureContext = {
  message: messageLookup({
    n: {
      short: "Two copies of the app run at once.",
      long: Array.from({ length: NOTICE_LONG_WORDS + 1 }, (_, index) => `word${String(index)}`).join(" "),
      title: "Needs a restart",
      plural: { one: "{count} item", other: "{count} items" },
    },
  }),
};

interface Case {
  /** A path the rule applies to. */
  path: string;
  /** Source with exactly `bad` breaches of the rule. */
  source: string;
  bad: number;
  /** The same thing written the way the rule asks for, which breaks nothing. */
  good: string;
}

const CASES: Readonly<Record<string, Case>> = {
  "feature-state": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <FeatureState state="on" title="x" />;',
    bad: 1,
    good: 'export const A = () => <Card status={<StatusPill state="running" label="On" size="sm" />} />;',
  },
  "badge-state": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => (<><Badge tone="warn">x</Badge><Badge tone={s.tone}>y</Badge><Badge tone={x ? "fail" : "neutral"}>z</Badge></>);',
    bad: 3,
    good: 'export const A = () => (<><Badge>x</Badge><Badge tone="accent">y</Badge><Badge tone={x ? "accent" : "neutral"}>z</Badge></>);',
  },
  "notice-stack": {
    path: "features/x/Page.tsx",
    source: "export const A = () => (<><Notice>a</Notice><Notice>b</Notice><Notice>c</Notice></>);",
    bad: 1,
    good: "export const A = () => (<><Notice>a</Notice><Notice>b</Notice></>);",
  },
  "notice-info-bare": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => (<><Notice>a</Notice><Notice tone="info">b</Notice></>);',
    bad: 2,
    good: 'export const A = () => (<><Notice title="t">a</Notice><Notice action={<b />}>b</Notice><Notice tone="warning">c</Notice></>);',
  },
  "notice-success": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <Notice tone="success">Saved</Notice>;',
    bad: 1,
    good: 'export const A = () => <Dialog title="x"><Notice tone="success">Saved, and here is what is next</Notice></Dialog>;',
  },
  "notice-long": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <Notice>{t("n.long")}</Notice>;',
    bad: 1,
    good: 'export const A = () => <Notice title={t("n.title")}>{t("n.short")}</Notice>;',
  },
  "switch-in-form": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => (<form><Switch label="x" /><Button type="submit">Save</Button></form>);',
    bad: 1,
    good: 'export const A = () => <Switch label="x" />;',
  },
  "card-save": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <Card title="x"><Button size="sm">{t("settings.shared.save")}</Button></Card>;',
    bad: 1,
    good: 'export const A = () => <Dialog title="x"><Card><Button size="sm">{t("settings.shared.save")}</Button></Card></Dialog>;',
  },
  "submit-disabled-invalid": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => (<><Button type="submit" disabled={name.trim() === ""}>x</Button><Button variant="primary" disabled={!valid}>y</Button></>);',
    bad: 2,
    good: 'export const A = () => (<><Button type="submit" disabled={!dirty}>x</Button><Button variant="primary" loading={pending}>y</Button><Button size="sm" disabled={rows.length === 0}>z</Button></>);',
  },
  "own-leave-dialog": {
    path: "features/x/Page.tsx",
    source: "export const A = () => { const blocker = useBlocker({ shouldBlockFn: () => dirty }); return <div />; };",
    bad: 1,
    good: "export const A = () => { useLeaveGuard(changes); return <div />; };",
  },
  "hand-aria-busy": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <div aria-busy="true" />;',
    bad: 1,
    good: 'export const A = () => <LoadingRegion label="x" />;',
  },
  "hand-error-ternary": {
    path: "features/x/Page.tsx",
    source: "export const A = () => (apps.isError && apps.data === undefined ? <ErrorBlock /> : null);",
    bad: 1,
    good: "export const A = () => <QueryState query={apps}>{() => null}</QueryState>;",
  },
  "inline-empty-by-hand": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <p className="text-13 text-fg-muted">{t("x.list.empty")}</p>;',
    bad: 1,
    good: 'export const A = () => <EmptyState variant="inline" title={t("x.list.empty")} />;',
  },
  "danger-button-outside-confirm": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <Button variant={on ? "danger" : "primary"}>x</Button>;',
    bad: 1,
    good: 'export const A = () => <Button variant="primary">x</Button>;',
  },
  "native-details": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <details className="group rounded-card border"><summary>x</summary></details>;',
    bad: 1,
    good: 'export const A = () => <Disclosure variant="card" label="x">y</Disclosure>;',
  },
  "row-actions-local": {
    path: "features/x/Page.tsx",
    source: "function RowActions() { return null; }\nconst Other = () => null;",
    bad: 1,
    good: 'const Other = () => <RowMenu name="x" />;',
  },
  "bordered-list": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <ul className="divide-y divide-border rounded-card border border-border">x</ul>;',
    bad: 1,
    good: 'export const A = () => <><RowList label="x">y</RowList><ul className="divide-y divide-border">z</ul></>;',
  },
  "row-link-class": {
    path: "features/x/Page.tsx",
    source: 'const LINK = "font-medium text-fg hover:underline hover:underline-offset-2";',
    bad: 1,
    good: 'export const A = () => <TextLink variant="row" to="/x">x</TextLink>;',
  },
  "copy-box-by-hand": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <code className="rounded-control border border-border bg-bg-sunken px-3 py-2 break-all select-all">{value}</code>;',
    bad: 1,
    good: 'export const A = () => <CopyValue value={value} label="x" />;',
  },
  "field-error-by-hand": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => (<><p className="text-13 text-fail">{e}</p><p role="alert">{e}</p></>);',
    bad: 2,
    good: 'export const A = () => <Field label="x" error={e}><Input /></Field>;',
  },
  "destination-outside-register": {
    path: "features/x/Tabs.tsx",
    source: 'import type { LinkTab } from "../../app/LinkTabs";\nconst tabs: LinkTab[] = [{ label: "a.b", to: "/a" }];',
    bad: 1,
    good: 'import type { LinkTab } from "../../app/LinkTabs";\nconst crumbs = { breadcrumbs: [{ label: "A", to: "/a" }] };',
  },
  "own-back-link": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <TextLink to="/x"><ChevronLeft aria-hidden="true" />{t("x.back")}</TextLink>;',
    bad: 1,
    good: 'export const A = () => <TextLink to="/x">{t("x.open")}</TextLink>;',
  },
  "empty-means-off": {
    path: "i18n/en/x.ts",
    source: 'export const x = { a: "How many to keep. Empty keeps every one.", b: "0 turns it off.", c: "Empty uses the default." } as const;',
    bad: 2,
    good: 'export const x = { a: "How many to keep, or no limit.", b: "Give a whole number." } as const;',
  },
  "help-starts-with-state": {
    path: "i18n/en/x.ts",
    source: 'export const x = { enabledHelp: "Off, it is kept but nothing is sent.", offNote: "Off: nothing runs.", title: "On, with a problem" } as const;',
    bad: 2,
    good: 'export const x = { enabledHelp: "Nothing is sent until a channel is set up.", title: "On, with a problem" } as const;',
  },
  "live-switch": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => <Switch label={t("x.logs.follow")} checked={on} onCheckedChange={set} />;',
    bad: 1,
    good: 'export const A = () => <Switch label={t("x.logs.onlyUnit")} checked={on} onCheckedChange={set} />;',
  },
  "toast-success": {
    path: "features/x/Page.tsx",
    source: 'export const A = () => { toast.success("Saved"); toast.success("Sent"); toast.error("No"); };',
    bad: 2,
    good: 'export const A = () => { toast.error("No"); };',
  },
};

describe("what each structural rule counts", () => {
  it("has an example for every rule, and none for a rule that does not exist", () => {
    expect(Object.keys(CASES).sort()).toEqual(STRUCTURE_RULES.map((rule) => rule.id).sort());
  });

  it.each(Object.entries(CASES))("%s: counts the breach and not the way the rule asks for", (rule, example) => {
    expect(countStructure(example.path, example.source, WORDS)[rule] ?? 0).toBe(example.bad);
    expect(countStructure(example.path, example.good, WORDS)[rule] ?? 0).toBe(0);
  });

  it("leaves the kit to write what a rule forbids everywhere else", () => {
    expect(countStructure("components/ui/Whatever.tsx", "export const A = () => <Notice>a</Notice>;", WORDS)["notice-info-bare"]).toBeUndefined();
    expect(countStructure("components/ui/ConfirmDialog.tsx", 'export const A = () => <Button variant="danger">x</Button>;', WORDS)["danger-button-outside-confirm"]).toBeUndefined();
    const register = 'import type { LinkTab } from "./LinkTabs";\nconst t: LinkTab[] = [{ label: "a", to: "/a" }];';
    expect(countStructure("app/nav.ts", register, WORDS)["destination-outside-register"]).toBeUndefined();
  });

  it("tells a catalog string of the English files from the same words elsewhere", () => {
    const source = 'export const x = { a: "Empty keeps every one." } as const;';
    expect(countStructure("i18n/es/x.ts", source, WORDS)["empty-means-off"]).toBeUndefined();
    expect(countStructure("features/x/Page.tsx", source, WORDS)["empty-means-off"]).toBeUndefined();
  });
});

describe("how the notice rules read", () => {
  it("counts the words of a notice in English from the catalog, title and body together", () => {
    expect(countWords("Two copies of the app run at once.")).toBe(8);
    expect(countWords("  {count} items ")).toBe(2);
    const long = 'export const A = () => <Notice title={t("n.title")}>{t("n.long")}</Notice>;';
    expect(countStructure("features/x/Page.tsx", long, WORDS)["notice-long"]).toBe(1);
  });

  it("counts only the longer side of a condition, and what it cannot read as nothing", () => {
    const either = 'export const A = () => <Notice>{x ? t("n.short") : t("n.title")}</Notice>;';
    expect(countStructure("features/x/Page.tsx", either, WORDS)["notice-long"]).toBeUndefined();
    const dynamic = "export const A = () => <Notice>{data.reason}</Notice>;";
    expect(countStructure("features/x/Page.tsx", dynamic, WORDS)["notice-long"]).toBeUndefined();
    // With no catalog to read, a rule that counts words counts nothing rather than guessing.
    expect(countStructure("features/x/Page.tsx", 'export const A = () => <Notice>{t("n.long")}</Notice>;')["notice-long"]).toBeUndefined();
  });

  it("excuses a file with a pile of notices by one design-exception anywhere in it", () => {
    const piled = [
      "// design-exception: notice-stack each one is a different condition of the same wizard step",
      "export const A = () => (<><Notice>a</Notice><Notice>b</Notice><Notice>c</Notice></>);",
    ].join("\n");
    expect(countStructure("features/x/Page.tsx", piled, WORDS)["notice-stack"]).toBeUndefined();
    const below = ["export const A = () => (<><Notice>a</Notice><Notice>b</Notice><Notice>c</Notice></>);", "", "", "// design-exception: notice-stack the reason"].join("\n");
    expect(countStructure("features/x/Page.tsx", below, WORDS)["notice-stack"]).toBeUndefined();
    const other = ["// design-exception: notice-long the reason", "export const A = () => (<><Notice>a</Notice><Notice>b</Notice><Notice>c</Notice></>);"].join("\n");
    expect(countStructure("features/x/Page.tsx", other, WORDS)["notice-stack"]).toBe(1);
  });

  it("lets an info notice with an action, a title or another tone stand", () => {
    const source = [
      "export const A = () => (<>",
      '<Notice tone="info" action={<TextLink />}>a</Notice>',
      '<Notice title="t">a</Notice>',
      "<Notice tone={tone}>a</Notice>",
      "</>);",
    ].join("\n");
    expect(countStructure("features/x/Page.tsx", source, WORDS)["notice-info-bare"]).toBeUndefined();
  });

  it("lets a success notice stand in a dialog, a drawer or a wizard and nowhere else", () => {
    for (const frame of ["Dialog", "Drawer", "ConfirmDialog", "Wizard"]) {
      const inside = `export const A = () => <${frame}><div><Notice tone="success">x</Notice></div></${frame}>;`;
      expect(countStructure("features/x/Page.tsx", inside, WORDS)["notice-success"], frame).toBeUndefined();
    }
    const card = 'export const A = () => <Card><Notice tone="success">x</Notice></Card>;';
    expect(countStructure("features/x/Page.tsx", card, WORDS)["notice-success"]).toBe(1);
  });
});
