import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH
  || (process.platform === "win32"
    ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
    : undefined);
const output = resolve("../../.pytmp/web-qa");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : {}),
  headless: true,
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 900 },
  colorScheme: "dark",
});

async function assertLayout(label) {
  const result = await page.evaluate(() => {
    const root = document.documentElement;
    const topbar = document.querySelector(".topbar")?.getBoundingClientRect();
    const workspace = document.querySelector(".workspace")?.getBoundingClientRect();
    const statusbar = document.querySelector(".statusbar")?.getBoundingClientRect();
    const unnamedButtons = [...document.querySelectorAll("button")]
      .filter((button) => button.getClientRects().length > 0)
      .filter((button) => !button.getAttribute("aria-label") && !button.getAttribute("title") && !button.textContent?.trim())
      .length;
    return {
      horizontalOverflow: root.scrollWidth - root.clientWidth,
      unnamedButtons,
      regionsPresent: Boolean(topbar && workspace && statusbar),
      regionsOverlap: Boolean(
        topbar && workspace && statusbar
        && (workspace.top < topbar.bottom - 1 || workspace.bottom > statusbar.top + 1)
      ),
    };
  });
  if (!result.regionsPresent) throw new Error(`${label}: required layout regions are missing`);
  if (result.horizontalOverflow > 1) throw new Error(`${label}: horizontal overflow ${result.horizontalOverflow}px`);
  if (result.regionsOverlap) throw new Error(`${label}: primary layout regions overlap`);
  if (result.unnamedButtons) throw new Error(`${label}: ${result.unnamedButtons} visible icon buttons have no accessible name`);
}

async function capture(name) {
  await assertLayout(name);
  const image = await page.screenshot({ path: resolve(output, name) });
  if (image.byteLength < 5_000) throw new Error(`${name}: screenshot is unexpectedly small`);
}

await page.goto("http://127.0.0.1:8765/#token=qa-token");
await page.getByText("开始一个编码任务").or(page.getByText("请检查项目结构")).waitFor();
await capture("dark-1440.png");

await page.reload();
await page.getByText("开始一个编码任务").or(page.getByText("请检查项目结构")).waitFor();
await capture("reload-1440.png");

await page.getByRole("button", { name: "计划", exact: true }).click();
await capture("plan-mode-1440.png");
await page.getByRole("button", { name: "执行", exact: true }).click();

await page.getByLabel("权限模式").selectOption("full-access");
await page.getByRole("alertdialog", { name: "启用完全信任" }).waitFor();
await capture("full-access-warning-1440.png");
await page.getByRole("button", { name: "取消", exact: true }).click();

await page.getByTitle("主题：system").click();
await capture("dark-explicit-1440.png");
await page.getByTitle("主题：dark").click();
await capture("light-1440.png");

await page.locator(".composer textarea").fill("Run the test suite and show the proposed changes.");
await page.locator(".send-button").click();
await page.locator(".approval-dialog").waitFor();
await capture("approval-1440.png");
await page.getByRole("button", { name: "拒绝", exact: true }).click();
await page.waitForTimeout(250);

await page.getByTitle("文件").click();
await page.getByTitle("src").click();
await page.getByTitle("src/app.py").click();
await page.getByLabel("src/app.py 只读预览").waitFor();

await page.setViewportSize({ width: 1024, height: 768 });
await page.waitForTimeout(250);
await capture("code-1024.png");
await page.setViewportSize({ width: 800, height: 700 });
await page.getByTitle("打开导航").click();
await page.waitForTimeout(250);
await capture("narrow-800.png");

await page.getByLabel("关闭面板").click();
await page.getByTitle("模型与设置").click();
await page.getByRole("dialog", { name: "模型设置" }).waitFor();
await page.getByLabel("服务商").selectOption("kimi-coding");
await page.locator(".field-grid select").selectOption("kimi-for-coding");
await page.getByText("密钥不能与开放平台混用").waitFor();
await page.getByLabel("思考强度").selectOption("low");
await capture("settings-kimi-coding-800.png");

await page.getByLabel("服务商").selectOption("kimi");
await page.locator(".field-grid select").selectOption("kimi-k2.7-code");
await capture("settings-kimi-open-800.png");

await browser.close();
