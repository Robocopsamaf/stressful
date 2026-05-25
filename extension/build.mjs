import { context, build } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

const args = process.argv.slice(2);
const targetArg = args.find((a) => a.startsWith("--target="));
const target = (targetArg ? targetArg.split("=")[1] : "firefox").toLowerCase();
if (!["firefox", "chrome"].includes(target)) {
  console.error(`unknown target: ${target}. expected firefox or chrome.`);
  process.exit(1);
}

const watch = args.includes("--watch");
const run = args.includes("--run");

const distDir = resolve(__dirname, `dist-${target}`);
const manifestSrc = resolve(__dirname, `manifest.${target}.json`);

const entryPoints = {
  background: "src/background.ts",
  content: "src/content.ts",
  options: "src/options.ts",
  popup: "src/popup.ts",
};

const baseOptions = {
  entryPoints,
  bundle: true,
  format: "iife",
  target: target === "firefox" ? ["firefox109"] : ["chrome111"],
  outdir: distDir,
  logLevel: "info",
  sourcemap: watch ? "inline" : false,
  minify: !watch,
};

async function copyStatic() {
  await mkdir(distDir, { recursive: true });
  await cp(manifestSrc, resolve(distDir, "manifest.json"));
  await cp(resolve(__dirname, "public"), distDir, { recursive: true });
}

await rm(distDir, { recursive: true, force: true });
await copyStatic();

if (watch) {
  const ctx = await context(baseOptions);
  await ctx.watch();
  console.log(`[build:${target}] watching for changes → ${distDir}`);
  if (run) {
    if (target !== "firefox") {
      console.warn("[build] --run only supported for firefox target");
    } else {
      const child = spawn(
        "npx",
        ["web-ext", "run", `--source-dir=${distDir}`, "--target=firefox-desktop"],
        { stdio: "inherit", cwd: __dirname }
      );
      child.on("exit", (code) => process.exit(code ?? 0));
    }
  }
} else {
  await build(baseOptions);
  console.log(`[build:${target}] done → ${distDir}`);
}
