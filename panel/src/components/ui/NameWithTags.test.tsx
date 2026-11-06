import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { NameWithTags } from "./NameWithTags";

describe("NameWithTags", () => {
  it("puts the first tags on the same line as the name and says how many wait behind the rest", () => {
    render(<NameWithTags name="tienda_prod" mono tags={["production", "shop", "clients", "payments"]} />);
    expect(screen.getByText("tienda_prod")).toBeInTheDocument();
    expect(screen.getByText("production")).toBeInTheDocument();
    expect(screen.getByText("shop")).toBeInTheDocument();
    expect(screen.queryByText("clients")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2 more tags: clients, payments" })).toHaveTextContent("+2");
  });

  it("opens every tag in a popover from the +N, by keyboard as well", async () => {
    const user = userEvent.setup();
    render(<NameWithTags name="tienda_prod" tags={["production", "shop", "clients", "payments"]} />);
    screen.getByRole("button", { name: /more tags/ }).focus();
    await user.keyboard("{Enter}");
    const popup = await screen.findByRole("dialog", { name: "4 tags" });
    for (const tag of ["production", "shop", "clients", "payments"]) expect(within(popup).getByText(tag)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("draws no +N when every tag fits, and no tags at all when there are none", () => {
    const { rerender } = render(<NameWithTags name="blog_wp" tags={["web"]} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("web")).toBeInTheDocument();
    rerender(<NameWithTags name="sessions" tags={[]} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("sessions")).toBeInTheDocument();
  });

  it("shows as many tags as it is told to before the +N", () => {
    render(<NameWithTags name="shop.example.com" tags={["a", "b", "c"]} shown={1} />);
    expect(screen.getByRole("button", { name: "2 more tags: b, c" })).toBeInTheDocument();
  });

  it("never stacks: the name and its tags are one line, and the name is what is cut, with the whole as its title", () => {
    render(<NameWithTags name="a-very-long-database-name-that-will-not-fit" tags={["production", "shop"]} />);
    const name = screen.getByText("a-very-long-database-name-that-will-not-fit");
    expect(name).toHaveAttribute("title", "a-very-long-database-name-that-will-not-fit");
    expect(name.className).toContain("truncate");
    const line = name.closest("[data-slot=name-with-tags]");
    expect(line).toHaveClass("flex");
    expect(line?.className).not.toContain("flex-col");
  });

  it("does not translate the name or the tags: they are the operator's words", () => {
    render(<NameWithTags name="tienda_prod" tags={["production"]} />);
    expect(screen.getByText("tienda_prod")).toHaveAttribute("translate", "no");
    expect(screen.getByText("production")).toHaveAttribute("translate", "no");
  });

  it("draws a tag as the link it stands for, in the same pebble, the +N included", async () => {
    const user = userEvent.setup();
    render(<NameWithTags name="ghcr.io" tags={["shop.example.com", "api.example.com", "queue-worker"]} shown={1} renderTag={(tag) => <a href={`#${tag}`}>{tag}</a>} />);
    expect(screen.getByRole("link", { name: "shop.example.com" })).toHaveAttribute("href", "#shop.example.com");
    await user.click(screen.getByRole("button", { name: /more tags/ }));
    const popup = await screen.findByRole("dialog");
    expect(within(popup).getByRole("link", { name: "queue-worker" })).toHaveAttribute("href", "#queue-worker");
  });

  it("draws the +N as text inside a control of its own, with the tags it stands for in its title and for a screen reader, since a button cannot hold a button", async () => {
    const { container } = render(
      <button type="button">
        <NameWithTags name="tienda_prod" tags={["production", "shop", "clients", "payments"]} inControl />
      </button>,
    );
    expect(screen.getAllByRole("button")).toHaveLength(1);
    const more = screen.getByText("+2");
    expect(more).toHaveAttribute("title", "clients, payments");
    expect(more).toHaveTextContent("2 more tags: clients, payments");
    await expectNoAxeViolations(container);
  });

  it("says the count in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<NameWithTags name="tienda_prod" tags={["a", "b", "c"]} />);
    expect(screen.getByRole("button", { name: "1 etiqueta más: c" })).toBeInTheDocument();
  });

  it("has no accessibility violations, with its popover open", async () => {
    const user = userEvent.setup();
    const { container } = render(<NameWithTags name="tienda_prod" mono tags={["production", "shop", "clients"]} />);
    await expectNoAxeViolations(container);
    await user.click(screen.getByRole("button", { name: /more tag/ }));
    await screen.findByRole("dialog");
    await expectNoAxeViolations(document.body);
  });
});
