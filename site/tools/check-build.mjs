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

function assertRelativeAssetUrls(relativePath) {
  const html = readFileSync(resolve(distDir, relativePath), "utf8");
  if (/(?:src|href)="\/(?!\/)/.test(html)) {
    throw new Error(
      `${relativePath} contains root-absolute asset URLs and will not work under the repository subpath.`,
    );
  }
  return html;
}

const indexHtml = assertRelativeAssetUrls("index.html");
assertRelativeAssetUrls("solo/index.html");

if (!indexHtml.includes('href="./solo/"')) {
  throw new Error(
    "Homepage does not link to ./solo/; the playable GitHub Pages entry is missing.",
  );
}

console.log("Composed Pages build is valid.");
