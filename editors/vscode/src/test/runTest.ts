import * as path from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  // Codex and some Electron hosts set this globally. VS Code must launch as
  // Electron, not as its embedded Node binary, for extension-host tests.
  delete process.env.ELECTRON_RUN_AS_NODE;
  const extensionDevelopmentPath = path.resolve(__dirname, "../..");
  const extensionTestsPath = path.resolve(__dirname, "suite", "index");
  await runTests({ extensionDevelopmentPath, extensionTestsPath });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
