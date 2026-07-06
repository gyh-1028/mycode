import * as assert from "node:assert";
import * as vscode from "vscode";

suite("MyCode extension", () => {
  test("registers public commands", async () => {
    const extension = vscode.extensions.getExtension("mycode-dev.mycode");
    assert.ok(extension);
    await extension.activate();
    const commands = await vscode.commands.getCommands(true);
    assert.ok(commands.includes("mycode.open"));
    assert.ok(commands.includes("mycode.newSession"));
    assert.ok(commands.includes("mycode.askSelection"));
  });
});
