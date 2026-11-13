import { describe, expect, it } from "vitest";

import { MAX_BLOCKS, judgeDestination, readInline, readReply } from "./replyText";

describe("judgeDestination", () => {
  it("lets a web address through with the host it really goes to", () => {
    expect(judgeDestination("https://docs.example.org/deploy?x=1")).toEqual({ href: "https://docs.example.org/deploy?x=1", host: "docs.example.org" });
    expect(judgeDestination("http://127.0.0.1:8080/")).toEqual({ href: "http://127.0.0.1:8080/", host: "127.0.0.1:8080" });
  });

  it.each([
    ["a script", "javascript:alert(1)"],
    ["a data document", "data:text/html,<script>alert(1)</script>"],
    ["a file", "file:///etc/passwd"],
    ["a relative path", "/apps/shop.example.com"],
    ["a protocol-relative address", "//evil.example.net/x"],
    ["nothing", ""],
    ["credentials that make one host read as another", "https://noust.example@evil.example.net/"],
    ["credentials with a password", "https://user:secret@evil.example.net/"],
  ])("refuses %s", (_name, destination) => {
    expect(judgeDestination(destination)).toBeNull();
  });
});

describe("readInline", () => {
  it("reads bold, code and a link, and keeps the text between them", () => {
    expect(readInline("Set **PORT** in `.env`, see [the docs](https://docs.example.org/).")).toEqual([
      { k: "text", text: "Set " },
      { k: "strong", text: "PORT" },
      { k: "text", text: " in " },
      { k: "code", text: ".env" },
      { k: "text", text: ", see " },
      { k: "link", label: "the docs", href: "https://docs.example.org/", host: "docs.example.org" },
      { k: "text", text: "." },
    ]);
  });

  it("never makes an image: it becomes a mark that says one was not shown", () => {
    expect(readInline("![pixel](https://tracker.example.net/p.gif)")).toEqual([{ k: "image", alt: "pixel" }]);
  });

  it("keeps a lying link as what it is: a label with the destination in view and no link", () => {
    expect(readInline("[click](javascript:alert(1))")).toEqual([{ k: "dead", label: "click", destination: "javascript:alert(1)" }]);
  });

  it("leaves a tag as the characters it is", () => {
    const [piece] = readInline('<img src=x onerror="alert(1)">');
    expect(piece).toEqual({ k: "text", text: '<img src=x onerror="alert(1)">' });
  });
});

describe("readReply", () => {
  it("reads paragraphs, both kinds of list and a fenced block of code", () => {
    const blocks = readReply(["First paragraph", "continues here.", "", "- one", "- two", "  and more", "", "1. step", "2. step", "", "```", "systemctl restart x", "```"].join("\n"));
    expect(blocks.map((block) => block.k)).toEqual(["p", "list", "list", "code"]);
    expect(blocks[0]).toEqual({ k: "p", inline: [{ k: "text", text: "First paragraph continues here." }] });
    expect(blocks[1]).toMatchObject({ k: "list", ordered: false });
    expect(blocks[2]).toMatchObject({ k: "list", ordered: true });
    expect(blocks[3]).toEqual({ k: "code", text: "systemctl restart x" });
    const first = blocks[1];
    if (first?.k === "list") expect(first.items[1]).toEqual([{ k: "text", text: "two and more" }]);
  });

  it("draws a heading as a paragraph in bold, never as a heading of the page", () => {
    expect(readReply("## What happened")).toEqual([{ k: "p", inline: [{ k: "strong", text: "What happened" }] }]);
  });

  it("does not let an unclosed fence swallow the rest as something else", () => {
    const blocks = readReply("```\nnever closed\nstill code");
    expect(blocks).toEqual([{ k: "code", text: "never closed\nstill code" }]);
  });

  it("is bounded: a reply of a hundred thousand paragraphs is not a hundred thousand elements", () => {
    const blocks = readReply(Array.from({ length: 100_000 }, (_, index) => `paragraph ${String(index)}`).join("\n\n"));
    expect(blocks.length).toBeLessThanOrEqual(MAX_BLOCKS);
  });

  it("reads an empty text as nothing", () => {
    expect(readReply("")).toEqual([]);
    expect(readReply("\n\n  \n")).toEqual([]);
  });
});
