import {
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  rmSync,
  statSync,
} from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Compose the GitHub Pages artifact after both applications build.
const scriptDir = dirname(fileURLToPath(import.meta.url));
const siteDir = resolve(scriptDir, "..");
const soloDist = resolve(siteDir, "../solo/dist");
const siteDist = resolve(siteDir, "dist");
const soloOutput = resolve(siteDist, "solo");

if (!existsSync(resolve(soloDist, "index.html"))) {
  throw new Error(
    `Missing ${resolve(soloDist, "index.html")}. Run "npm --prefix solo run build" first.`,
  );
}

rmSync(soloOutput, { recursive: true, force: true });
mkdirSync(siteDist, { recursive: true });
cpSync(soloDist, soloOutput, { recursive: true });

function countFiles(directory) {
  return readdirSync(directory).reduce((count, entry) => {
    const path = resolve(directory, entry);
    return count + (statSync(path).isDirectory() ? countFiles(path) : 1);
  }, 0);
}

console.log(
  `Prepared ${countFiles(soloOutput)} single-player files at ${soloOutput}`,
);
