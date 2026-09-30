import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Fail closed when the composed Pages artifact is incomplete or non-portable.
const scriptDir = dirname(fileURLToPath(import.meta.url));
const siteDir = resolve(scriptDir, "..");
const distDir = resolve(siteDir, "dist");

const required = [
  "index.html",
  "assets",
  "solo/index.html",
  "solo/sw.js",
  "solo/manifest.webmanifest",
];

const missing = required.filter((path) => !existsSync(resolve(distDir, path)));
if (missing.length > 0) {
  throw new Error(`Missing build output: ${missing.join(", ")}`);
}

const indexHtml = readFileSync(resolve(distDir, "index.html"), "utf8");
if (/(?:src|href)="\/(?!\/)/.test(indexHtml)) {
  throw new Error(
    "The composed site contains root-absolute asset URLs and will not work under the repository subpath.",
  );
}

if (!indexHtml.includes('href="./solo/"')) {
  console.warn(
    "Warning: homepage does not yet link to ./solo/; Task 3 must add the playable entry.",
  );
}

console.log("Composed Pages build is valid.");
