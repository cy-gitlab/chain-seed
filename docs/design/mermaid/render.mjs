import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const toolDir = dirname(fileURLToPath(import.meta.url));
const docsDir = resolve(toolDir, "..");
const outputDir = resolve(toolDir, "rendered");
const mmdc = resolve(toolDir, "node_modules", ".bin", "mmdc");
const puppeteerConfig = resolve(toolDir, "puppeteer-config.json");
const documents = [
  "architecture.md",
  "architecture.zh-CN.md",
  "seedemu-integration.md",
  "seedemu-integration.zh-CN.md",
].map((name) => join(docsDir, name));

rmSync(outputDir, { force: true, recursive: true });
mkdirSync(outputDir, { recursive: true });

let rendered = 0;

for (const document of documents) {
  const markdown = readFileSync(document, "utf8");
  const diagrams = [...markdown.matchAll(/```mermaid[ \t]*\r?\n([\s\S]*?)```/g)];
  const stem = basename(document, ".md");

  diagrams.forEach((match, index) => {
    const id = String(index + 1).padStart(2, "0");
    const input = join(outputDir, `${stem}-${id}.mmd`);
    const output = join(outputDir, `${stem}-${id}.svg`);

    writeFileSync(input, match[1].trimEnd() + "\n");

    const result = spawnSync(
      mmdc,
      [
        "--input",
        input,
        "--output",
        output,
        "--backgroundColor",
        "transparent",
        "--puppeteerConfigFile",
        puppeteerConfig,
      ],
      { encoding: "utf8", stdio: "pipe" },
    );

    if (result.status !== 0) {
      process.stderr.write(`Failed to render ${document} diagram ${id}\n`);
      process.stderr.write(result.stdout);
      process.stderr.write(result.stderr);
      process.exit(result.status ?? 1);
    }

    rendered += 1;
    process.stdout.write(`rendered ${basename(output)}\n`);
  });
}

process.stdout.write(`validated ${rendered} Mermaid diagrams\n`);
