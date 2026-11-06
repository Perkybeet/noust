/**
 * Settings > Sign-in image: the picture an operator puts where Noust draws its own noust on the screens
 * before the console (item 129): a company's logo, or any picture. It is seen by anyone who opens the
 * console, so the page says so; and it is the sign-in policy's, so without that permission it only reads.
 */
export const signInImage = {
  nav: {
    label: "Sign-in image",
    keywords: "sign in login picture image logo brand branding company photo artwork screen welcome appearance",
    command: "Sign-in image settings",
  },
  documentTitle: "Sign-in image",
  loading: "Loading the sign-in image",
  loadFailed: "Could not read the sign-in image",
  refreshFailed: "Could not refresh the sign-in image",
  /** The one line that says who sees it. */
  public: "It is seen before signing in: anyone who opens the console sees it.",
  /** The line a person without the permission reads once, at the top. */
  readOnly: "You can see this page, not change it. Whoever looks after the console's security does.",
  locked: {
    reason: "What is seen before signing in is part of how the console is protected. It is changed by whoever holds the permission {permission}.",
  },
  preview: {
    label: "How the sign-in screen looks",
    none: "Noust's own drawing, as it is now",
    chosen: "{name}, not saved yet",
    saved: "The picture that is set now",
  },
  fit: {
    label: "How it fills the space",
    cover: "Fill the space",
    contain: "Fit it whole",
    coverHelp: "For a photograph: it covers the space and what does not fit is cropped.",
    containHelp: "For a logo: all of it is shown, on the surface of the space.",
  },
  alt: {
    label: "Description",
    help: "What a screen reader says in place of the image. Leave it empty if it is only decoration.",
    placeholder: "Acme logo",
  },
  file: {
    limits: "PNG, JPEG or WebP, up to {size} and {side} pixels on a side.",
    drop: "Drop an image on the screen above, or choose one.",
    dropActive: "Drop it to see it on the screen",
  },
  actions: {
    choose: "Choose an image",
    change: "Change the image",
    remove: "Remove the image",
  },
  remove: {
    title: "Remove the sign-in image?",
    description: "The screen goes back to Noust's own drawing. The file is deleted from this server.",
    action: "Remove the image",
  },
  saveFailed: "Could not save the sign-in image",
  errors: {
    tooLarge: "That file weighs {size} and the most is {limit}.",
    notAllowed: "Choose a PNG, JPEG or WebP. An SVG is not accepted: it is code, and this image is seen by anyone.",
    unreadable: "This file could not be read.",
    notShown: "This file does not open as an image.",
  },
} as const;
