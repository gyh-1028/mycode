import * as esbuild from "esbuild";
import { cp, mkdir } from "node:fs/promises";

const watch = process.argv.includes("--watch");
const common = { bundle: true, sourcemap: true, minify: false, logLevel: "info" };
const builds = [
  {
    ...common,
    entryPoints: ["src/extension.ts"],
    outfile: "dist/extension.js",
    platform: "node",
    format: "cjs",
    external: ["vscode"],
  },
  {
    ...common,
    entryPoints: ["src/webview/index.tsx"],
    outfile: "dist/webview.js",
    platform: "browser",
    format: "iife",
    minify: true,
  },
  {
    ...common,
    entryPoints: ["src/test/runTest.ts"],
    outfile: "dist/test/runTest.js",
    platform: "node",
    format: "cjs",
  },
  {
    ...common,
    entryPoints: ["src/test/suite/index.ts"],
    outfile: "dist/test/suite/index.js",
    platform: "node",
    format: "cjs",
    external: ["vscode", "mocha"],
  },
  {
    ...common,
    entryPoints: ["src/test/suite/extension.test.ts"],
    outfile: "dist/test/suite/extension.test.js",
    platform: "node",
    format: "cjs",
    external: ["vscode"],
  },
];

if (watch) {
  await Promise.all(builds.map(async (options) => (await esbuild.context(options)).watch()));
  console.log("Watching MyCode extension sources...");
} else {
  await Promise.all(builds.map((options) => esbuild.build(options)));
}

await mkdir("dist/codicons", { recursive: true });
await Promise.all([
  cp("node_modules/@vscode/codicons/dist/codicon.css", "dist/codicons/codicon.css"),
  cp("node_modules/@vscode/codicons/dist/codicon.ttf", "dist/codicons/codicon.ttf"),
  cp("../../LICENSE", "LICENSE"),
]);
