// A client-side application: Noust builds it with `ng build` and serves dist/hello/browser as files.
import { Component, provideZonelessChangeDetection } from "@angular/core";
import { bootstrapApplication } from "@angular/platform-browser";

@Component({ selector: "app-root", template: "<h1>Hello from Noust (angular)</h1>" })
class App {}

bootstrapApplication(App, { providers: [provideZonelessChangeDetection()] }).catch((error) =>
  console.error(error),
);
