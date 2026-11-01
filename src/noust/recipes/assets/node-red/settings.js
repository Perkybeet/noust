// Noust: the editor and its API ask for a user, and the password comes from the environment.
module.exports = {
  flowFile: "flows.json",
  credentialSecret: process.env.CREDENTIAL_SECRET,
  adminAuth: {
    type: "credentials",
    users: [{ username: "admin", password: process.env.ADMIN_HASH, permissions: "*" }],
  },
  functionExternalModules: true,
  logging: { console: { level: "info", metrics: false, audit: false } },
};
