import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelSettingsDialog } from "./ModelSettingsDialog";
import type { ModelCatalog, ModelFormState } from "./protocol";

const catalog: ModelCatalog = {
  id: "deepseek",
  label: "DeepSeek",
  provider: "openai",
  baseUrl: "https://api.deepseek.com",
  apiKeyEnv: "DEEPSEEK_API_KEY",
  defaultModel: "deepseek-v4-flash",
  thinkingFormat: "standard",
  reasoningEfforts: ["high", "max"],
  models: [
    { id: "deepseek-v4-flash", label: "DeepSeek V4 Flash", description: "快速", thinking: "toggle" },
    { id: "deepseek-v4-pro", label: "DeepSeek V4 Pro", description: "高能力", thinking: "toggle" },
  ],
};

const form: ModelFormState = {
  catalogId: "deepseek",
  name: "deepseek-v4-flash",
  provider: "openai",
  model: "deepseek-v4-flash",
  baseUrl: catalog.baseUrl,
  apiKeyEnv: catalog.apiKeyEnv,
  apiKey: "",
  thinking: "",
  thinkingFormat: "standard",
  thinkingBudget: "",
  reasoningEffort: "",
};

afterEach(cleanup);

describe("ModelSettingsDialog", () => {
  it("shows provider models and exposes thinking controls", () => {
    const onModelChange = vi.fn();
    const onFormChange = vi.fn();
    render(<ModelSettingsDialog
      profiles={[]}
      presets={[]}
      catalogs={[catalog]}
      form={form}
      busy={false}
      onClose={vi.fn()}
      onFormChange={onFormChange}
      onProviderChange={vi.fn()}
      onModelChange={onModelChange}
      onSave={vi.fn()}
      onUse={vi.fn()}
      onDeleteRequest={vi.fn()}
      onRemove={vi.fn()}
    />);

    expect(screen.getByRole("option", { name: "DeepSeek V4 Pro" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("模型"), { target: { value: "deepseek-v4-pro" } });
    expect(onModelChange).toHaveBeenCalledWith("deepseek-v4-pro");

    fireEvent.click(screen.getByRole("button", { name: "思考" }));
    expect(onFormChange).toHaveBeenCalledWith(expect.objectContaining({ thinking: "enabled" }));
  });

  it("renders effort-only controls and qwen thinking budget", () => {
    const effortCatalog: ModelCatalog = {
      ...catalog,
      id: "openai",
      label: "OpenAI GPT",
      defaultModel: "gpt-5.4-mini",
      thinkingFormat: "openai",
      reasoningEfforts: ["none", "low", "medium", "high"],
      models: [{ id: "gpt-5.4-mini", label: "GPT-5.4 mini", description: "平衡", thinking: "effort" }],
    };
    const effortForm = { ...form, catalogId: "openai", model: "gpt-5.4-mini", name: "gpt-5.4-mini", thinkingFormat: "openai" };
    const { unmount } = render(<ModelSettingsDialog
      profiles={[]} presets={[]} catalogs={[effortCatalog]} form={effortForm} busy={false}
      onClose={vi.fn()} onFormChange={vi.fn()} onProviderChange={vi.fn()} onModelChange={vi.fn()}
      onSave={vi.fn()} onUse={vi.fn()} onDeleteRequest={vi.fn()} onRemove={vi.fn()}
    />);
    expect(screen.getByLabelText("推理程度")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "思考" })).not.toBeInTheDocument();
    unmount();

    const qwenCatalog: ModelCatalog = {
      ...catalog,
      id: "qwen",
      label: "千问",
      thinkingFormat: "qwen",
      thinkingBudget: { min: 0, default: 8192, step: 1024 },
    };
    render(<ModelSettingsDialog
      profiles={[]} presets={[]} catalogs={[qwenCatalog]}
      form={{ ...form, catalogId: "qwen", thinking: "enabled", thinkingFormat: "qwen" }} busy={false}
      onClose={vi.fn()} onFormChange={vi.fn()} onProviderChange={vi.fn()} onModelChange={vi.fn()}
      onSave={vi.fn()} onUse={vi.fn()} onDeleteRequest={vi.fn()} onRemove={vi.fn()}
    />);
    expect(screen.getByLabelText("思考预算（Token）")).toBeInTheDocument();
  });
});
