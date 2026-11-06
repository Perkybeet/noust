import { describe, expect, it } from "vitest";

import type { CertEntry } from "../../api/queries/certs";
import type { SiteEntry } from "../../api/queries/sites";
import { certificateCovering, domainRows, matchesDomain, roleOf, wildcardCovers } from "./domainRows";

function site(name: string, extra: Partial<SiteEntry> = {}): SiteEntry {
  return {
    name,
    webserver: "nginx",
    enabled: true,
    config_path: `/etc/nginx/sites-available/${name}`,
    has_ssl: true,
    server_names: [name],
    noust_managed: true,
    app: name,
    site_name: name,
    pending_reload: false,
    edited_outside: false,
    ...extra,
  };
}

function cert(domain: string, domains: string[] = [domain], days = 40): CertEntry {
  return { domain, domains, days_remaining: days, expires_on: "2027-01-01", source: "letsencrypt" } as CertEntry;
}

describe("wildcardCovers", () => {
  it("covers one label under the wildcard and nothing deeper or bare", () => {
    expect(wildcardCovers("*.example.com", "shop.example.com")).toBe(true);
    expect(wildcardCovers("*.example.com", "a.b.example.com")).toBe(false);
    expect(wildcardCovers("*.example.com", "example.com")).toBe(false);
    expect(wildcardCovers("example.com", "example.com")).toBe(false);
  });
});

describe("certificateCovering", () => {
  it("finds the certificate that lists the name, and then the one that covers it by wildcard", () => {
    const own = cert("shop.example.com");
    const wild = cert("example.com", ["example.com", "*.example.com"]);
    expect(certificateCovering([wild, own], "shop.example.com")).toBe(own);
    expect(certificateCovering([wild], "api.example.com")).toBe(wild);
    expect(certificateCovering([wild], "other.net")).toBeNull();
  });

  it("takes the one that lasts longest when two cover the same name", () => {
    const soon = cert("shop.example.com", ["shop.example.com"], 3);
    const later = cert("shop.example.com", ["shop.example.com"], 70);
    expect(certificateCovering([soon, later], "shop.example.com")).toBe(later);
  });
});

describe("domainRows", () => {
  it("has one row for each name a site answers, with the application and the certificate that covers it", () => {
    const rows = domainRows(
      [site("blog.example.com", { server_names: ["blog.example.com", "news.example.com"] }), site("api.example.com", { app: null, noust_managed: false })],
      [cert("blog.example.com", ["blog.example.com", "news.example.com"], 12)],
    );
    expect(rows.map((row) => row.name)).toEqual(["api.example.com", "blog.example.com", "news.example.com"]);
    const [api, blog, news] = rows;
    expect(api?.app).toBeNull();
    expect(api?.cert).toBeNull();
    expect(blog?.app).toBe("blog.example.com");
    expect(blog?.cert?.days_remaining).toBe(12);
    expect(news?.site).toBe("blog.example.com");
    expect(news?.cert?.domain).toBe("blog.example.com");
  });

  it("says a name is served over HTTPS without a certificate of the list when the site has TLS and nothing covers it", () => {
    const [row] = domainRows([site("shop.example.com", { has_ssl: true })], []);
    expect(row?.secure).toBe(true);
    expect(row?.cert).toBeNull();
    const [plain] = domainRows([site("shop.example.com", { has_ssl: false })], []);
    expect(plain?.secure).toBe(false);
  });

  it("keeps the enabled site's row when two sites answer the same name", () => {
    const rows = domainRows([site("old.example.com", { enabled: false, server_names: ["shop.example.com"], app: null }), site("shop.example.com")], []);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.enabled).toBe(true);
    expect(rows[0]?.site).toBe("shop.example.com");
  });

  it("leaves out the catch-all names that are not a domain", () => {
    expect(domainRows([site("default", { server_names: ["_", "", "default_server"] })], [])).toEqual([]);
  });

  it("lists a wildcard name as it is", () => {
    const [row] = domainRows([site("wild.example.com", { server_names: ["*.example.com"] })], []);
    expect(row?.name).toBe("*.example.com");
  });
});

describe("roleOf", () => {
  it("is a redirect for a name the application's own list sends to it, whichever site holds it", () => {
    const rows = domainRows([site("shop.example.com", { server_names: ["shop.example.com", "www.shop.example.com"] }), site("files.example.com", { app: null })], []);
    const shop = rows.find((row) => row.name === "shop.example.com");
    const www = rows.find((row) => row.name === "www.shop.example.com");
    if (shop === undefined || www === undefined) throw new Error("no rows");

    expect(roleOf(shop, new Set(["www.shop.example.com"]))).toBe("app");
    expect(roleOf(www, new Set(["www.shop.example.com"]))).toBe("redirect");
    expect(roleOf(www, new Set())).toBe("app");
  });

  it("is a site of its own when no application has the name", () => {
    const [row] = domainRows([site("files.example.com", { app: null })], []);
    if (row === undefined) throw new Error("no row");

    expect(roleOf(row, new Set())).toBe("site");
    // A redirect needs an application to redirect to: a name with none is the site's, whatever a list says.
    expect(roleOf(row, new Set(["files.example.com"]))).toBe("site");
  });
});

describe("matchesDomain", () => {
  it("matches the name, its application and the site's file, ignoring case", () => {
    const [row] = domainRows([site("shop.example.com", { app: "shop-app" })], []);
    expect(row).toBeDefined();
    if (row === undefined) return;
    expect(matchesDomain(row, "SHOP")).toBe(true);
    expect(matchesDomain(row, "shop-app")).toBe(true);
    expect(matchesDomain(row, "zzz")).toBe(false);
    expect(matchesDomain(row, "")).toBe(true);
  });
});
