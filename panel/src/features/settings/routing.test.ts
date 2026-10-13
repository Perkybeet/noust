import { describe, expect, it } from "vitest";

import type { NotificationChannel } from "../../api/queries/notifications";
import { bindT } from "../../i18n";
import { EVENT_KINDS } from "./notifications";
import { isClock, parseList, quietBlock, routeBlock, routeSummary, routingBody, routingDirty, routingFormFrom } from "./routing";

const en = bindT("en");

function channel(partial: Partial<NotificationChannel> = {}): NotificationChannel {
  return { name: "oncall", type: "pushover", enabled: true, configured: true, default: false, settings: {}, events: {}, route: {}, quiet: null, ...partial };
}

const SERVER = { deploy_started: false, deploy_failed: true };

describe("a channel's routing", () => {
  it("reads lists typed with commas, spaces or lines, without blanks or repeats", () => {
    expect(parseList("shop.example.com, *.staging.example.com\n shop.example.com  ")).toEqual(["shop.example.com", "*.staging.example.com"]);
    expect(parseList("   ")).toEqual([]);
  });

  it("starts from what the channel has: nothing narrowed, the server's events, no quiet hours", () => {
    const form = routingFormFrom(channel(), SERVER);
    expect(form.ownEvents).toBe(false);
    expect(form.route).toEqual({ apps: "", projects: "", environments: "", tags: "", minSeverity: "", serverEvents: false });
    expect(form.quiet.on).toBe(false);
    // An event the server has no switch for is on, but for the few that ship off.
    expect(form.events["deploy_failed"]).toBe(true);
    expect(form.events["deploy_started"]).toBe(false);
    expect(form.events["restore_failed"]).toBe(true);
    expect(form.events["cron_success"]).toBe(false);
  });

  it("starts from the channel's own route, events and quiet hours when it has them", () => {
    const form = routingFormFrom(
      channel({
        events: { deploy_started: true, backup_failed: false },
        route: { apps: ["shop.example.com"], projects: ["shop"], min_severity: "failed", server_events: true },
        quiet: { start: "22:00", end: "07:00", timezone: "Europe/Madrid", allow: "warning", digest: false },
      }),
      SERVER,
    );
    expect(form.ownEvents).toBe(true);
    expect(form.events["deploy_started"]).toBe(true);
    expect(form.events["backup_failed"]).toBe(false);
    expect(form.route).toMatchObject({ apps: "shop.example.com", projects: "shop", minSeverity: "failed", serverEvents: true });
    expect(form.quiet).toEqual({ on: true, start: "22:00", end: "07:00", timezone: "Europe/Madrid", allow: "warning", digest: false });
  });

  it("writes only what is set, and clears what the form emptied", () => {
    const empty = routingBody(routingFormFrom(channel(), SERVER), channel());
    // An empty object is how the API clears events, a route and quiet hours.
    expect(empty).toEqual({ events: {}, route: {}, quiet: {} });

    const form = routingFormFrom(channel(), SERVER);
    form.route = { apps: "shop.example.com, *.staging.example.com", projects: "", environments: "shop/staging", tags: "critical", minSeverity: "warning", serverEvents: true };
    form.quiet = { on: true, start: "22:00", end: "07:00", timezone: " ", allow: "failed", digest: true };
    expect(routingBody(form, channel()).route).toEqual({
      apps: ["shop.example.com", "*.staging.example.com"],
      environments: ["shop/staging"],
      tags: ["critical"],
      min_severity: "warning",
      server_events: true,
    });
    // A blank time zone is UTC, which is what the server reads it as too.
    expect(routingBody(form, channel()).quiet).toEqual({ start: "22:00", end: "07:00", timezone: "UTC", allow: "failed", digest: true });
  });

  it("writes every event's switch when the channel has its own, and none when it follows the server", () => {
    const form = routingFormFrom(channel(), SERVER);
    expect(routingBody(form, channel()).events).toEqual({});
    form.ownEvents = true;
    form.events = { ...form.events, deploy_started: true, backup_failed: false };
    const written = routingBody(form, channel()).events ?? {};
    expect(Object.keys(written)).toEqual([...EVENT_KINDS]);
    expect(written["deploy_started"]).toBe(true);
    expect(written["backup_failed"]).toBe(false);
  });

  it("carries a Telegram channel's topic as its setting, and no other channel's", () => {
    const telegram = channel({ type: "telegram", settings: { thread_id: 12 } });
    const form = routingFormFrom(telegram, SERVER);
    expect(form.topic).toBe("12");
    form.topic = " 7 ";
    expect(routingBody(form, telegram).settings).toEqual({ thread_id: "7" });
    form.topic = "";
    expect(routingBody(form, telegram).settings).toEqual({ thread_id: "" });
    expect(routingBody(routingFormFrom(channel(), SERVER), channel()).settings).toBeUndefined();
  });

  it("is dirty only when what would be written differs", () => {
    const initial = routingFormFrom(channel(), SERVER);
    expect(routingDirty(initial, initial, channel())).toBe(false);
    // Opening the events to choose and changing nothing writes the same server-following channel...
    expect(routingDirty({ ...initial, ownEvents: true }, initial, channel())).toBe(true);
    expect(routingDirty({ ...initial, route: { ...initial.route, tags: "critical" } }, initial, channel())).toBe(true);
    // ...and a quiet-hours form that is off is the same however its fields were left.
    expect(routingDirty({ ...initial, quiet: { ...initial.quiet, start: "01:00" } }, initial, channel())).toBe(false);
  });

  it("checks a clock time the way the server reads one", () => {
    expect(["22:00", "7:05", "00:00", "23:59"].every(isClock)).toBe(true);
    expect(["24:00", "7:5", "noon", "", "22:60"].some(isClock)).toBe(false);
  });

  it("builds the blocks from a form's pieces", () => {
    expect(routeBlock({ apps: "", projects: "", environments: "", tags: "", minSeverity: "", serverEvents: false })).toEqual({});
    expect(quietBlock({ on: false, start: "22:00", end: "07:00", timezone: "UTC", allow: "failed", digest: true })).toEqual({});
  });

  it("says what a channel is limited to, and nothing for one that hears everything", () => {
    expect(routeSummary(en, channel())).toEqual([]);
    expect(
      routeSummary(
        en,
        channel({
          events: { deploy_failed: true },
          route: { apps: ["shop.example.com"], tags: ["critical"], min_severity: "failed" },
          quiet: { start: "22:00", end: "07:00" },
        }),
      ),
    ).toEqual(["Applications: shop.example.com", "Tags: critical", "Failures only", "Quiet from 22:00 to 07:00", "Own switch for 1 event"]);
  });
});
