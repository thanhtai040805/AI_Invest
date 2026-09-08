const userAgent = process.env.npm_config_user_agent || "";

if (!userAgent.startsWith("npm/")) {
  console.error("This package must be installed with npm so package-lock.json stays authoritative.");
  process.exit(1);
}
