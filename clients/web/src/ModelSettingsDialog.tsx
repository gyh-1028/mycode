import { BrainCircuit, KeyRound, Sparkles, Trash2, X, Zap } from "lucide-react";
import type { ModelCatalog, ModelFormState, ModelProfile } from "./protocol";

interface ModelSettingsDialogProps {
  profiles: ModelProfile[];
  presets: ModelProfile[];
  catalogs: ModelCatalog[];
  form: ModelFormState;
  busy: boolean;
  pendingDelete?: string;
  onClose: () => void;
  onFormChange: (form: ModelFormState) => void;
  onProviderChange: (id: string) => void;
  onModelChange: (model: string) => void;
  onSave: (event: React.FormEvent) => void;
  onUse: (name: string) => void;
  onDeleteRequest: (name?: string) => void;
  onRemove: (name: string) => void;
}

function thinkingLabel(profile: ModelProfile): string {
  if (profile.reasoningEffort && !profile.thinking) return `推理 · ${profile.reasoningEffort}`;
  if (profile.thinking === "enabled") {
    return profile.reasoningEffort ? `思考 · ${profile.reasoningEffort}` : "思考模式";
  }
  if (profile.thinking === "disabled") return "快速模式";
  return "模型默认";
}

export function ModelSettingsDialog({
  profiles,
  presets,
  catalogs,
  form,
  busy,
  pendingDelete,
  onClose,
  onFormChange,
  onProviderChange,
  onModelChange,
  onSave,
  onUse,
  onDeleteRequest,
  onRemove,
}: ModelSettingsDialogProps): React.JSX.Element {
  const catalog = catalogs.find((item) => item.id === form.catalogId);
  const selectedModel = catalog?.models.find((item) => item.id === form.model);
  const fixedThinking = selectedModel?.thinking === "enabled" || selectedModel?.thinking === "disabled";
  const effortOnly = selectedModel?.thinking === "effort";
  const reasoningEfforts = selectedModel?.reasoningEfforts ?? catalog?.reasoningEfforts ?? [];
  const uncataloguedPresets = presets.filter((preset) => !catalogs.some((item) => item.id === preset.name));

  function setForm(update: Partial<ModelFormState>): void {
    onFormChange({ ...form, ...update });
  }

  return <div className="modal-backdrop" role="presentation">
    <section className="settings-dialog" role="dialog" aria-modal="true" aria-label="模型设置">
      <header>
        <div><h2>模型与凭据</h2><p>选择服务商、具体模型和思考方式。API Key 只写入系统凭据库。</p></div>
        <button className="icon-button" onClick={onClose} title="关闭"><X size={18} /></button>
      </header>
      <div className="settings-body">
        <div className="profile-list">
          <h3>已保存配置</h3>
          {profiles.length === 0 && <div className="settings-empty">还没有模型配置</div>}
          {profiles.map((profile) => <div className={`profile-row ${profile.active ? "active" : ""}`} key={profile.name}>
            <div>
              <strong>{profile.name}</strong>
              <span>{profile.model}</span>
              <small>{thinkingLabel(profile)} · {profile.keyConfigured ? profile.keySource : "密钥未配置"}</small>
            </div>
            <button disabled={profile.active || busy} onClick={() => onUse(profile.name)}>{profile.active ? "当前" : "使用"}</button>
            <button className="icon-button" disabled={profile.active || busy} onClick={() => onDeleteRequest(`model:${profile.name}`)} title="删除配置"><Trash2 size={14} /></button>
            {pendingDelete === `model:${profile.name}` && <div className="inline-confirm"><span>同时删除凭据？</span><button onClick={() => onDeleteRequest()}>取消</button><button className="danger" onClick={() => onRemove(profile.name)}>删除</button></div>}
          </div>)}
        </div>

        <form className="model-form" onSubmit={onSave}>
          <h3>新增或更新</h3>
          <label>服务商
            <select value={form.catalogId} onChange={(event) => onProviderChange(event.target.value)}>
              <option value="">自定义 OpenAI 兼容服务</option>
              {catalogs.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              {uncataloguedPresets.map((preset) => <option key={preset.name} value={`preset:${preset.name}`}>{preset.name}</option>)}
            </select>
          </label>

          <div className="field-grid">
            <label>配置名称<input required value={form.name} onChange={(event) => setForm({ name: event.target.value })} placeholder="例如：deepseek-pro" /></label>
            <label>模型
              {catalog
                ? <select required value={form.model} onChange={(event) => onModelChange(event.target.value)}>{catalog.models.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
                : <input required value={form.model} onChange={(event) => setForm({ model: event.target.value })} placeholder="API model ID" />}
            </label>
          </div>

          {selectedModel && <div className={`model-description ${selectedModel.deprecated ? "deprecated" : ""}`}><Sparkles size={14} /><span>{selectedModel.description}</span></div>}

          {selectedModel && selectedModel.thinking !== "none" && !effortOnly && <fieldset className="thinking-field">
            <legend>思考模式</legend>
            <div className="mode-control" aria-label="思考模式">
              <button type="button" className={form.thinking === "" ? "active" : ""} disabled={fixedThinking} onClick={() => setForm({ thinking: "", reasoningEffort: "" })}><Sparkles size={14} />默认</button>
              <button type="button" className={form.thinking === "enabled" ? "active" : ""} disabled={fixedThinking || selectedModel.thinking === "disabled"} onClick={() => setForm({ thinking: "enabled" })}><BrainCircuit size={14} />思考</button>
              <button type="button" className={form.thinking === "disabled" ? "active" : ""} disabled={fixedThinking || selectedModel.thinking === "enabled"} onClick={() => setForm({ thinking: "disabled", reasoningEffort: "" })}><Zap size={14} />快速</button>
            </div>
            {fixedThinking && <small>该兼容模型名的思考模式固定，无法切换。</small>}
          </fieldset>}

          {reasoningEfforts.length > 0 && form.thinking !== "disabled" && <label>{effortOnly ? "推理程度" : "思考强度"}
            <select value={form.reasoningEffort} onChange={(event) => setForm({ reasoningEffort: event.target.value })}>
              <option value="">模型默认</option>
              {reasoningEfforts.map((effort) => <option key={effort} value={effort}>{effortLabel(effort)}</option>)}
            </select>
          </label>}

          {catalog?.thinkingBudget && form.thinking === "enabled" && <label>思考预算（Token）
            <input
              type="number"
              min={catalog.thinkingBudget.min}
              step={catalog.thinkingBudget.step}
              value={form.thinkingBudget}
              onChange={(event) => setForm({ thinkingBudget: event.target.value })}
              placeholder={`模型默认（建议 ${catalog.thinkingBudget.default}）`}
            />
          </label>}

          <label>API Key
            <span className="secret-input"><KeyRound size={14} /><input type="password" autoComplete="new-password" value={form.apiKey} onChange={(event) => setForm({ apiKey: event.target.value })} placeholder="留空则不更改" /></span>
          </label>

          <details className="advanced-settings">
            <summary>高级连接设置</summary>
            <div className="advanced-fields">
              <label>Provider<input required value={form.provider} onChange={(event) => setForm({ provider: event.target.value })} /></label>
              <label>Base URL<input value={form.baseUrl} onChange={(event) => setForm({ baseUrl: event.target.value })} placeholder="可选" /></label>
              <label>环境变量<input value={form.apiKeyEnv} onChange={(event) => setForm({ apiKeyEnv: event.target.value })} placeholder="存在时优先" /></label>
            </div>
          </details>

          <button className="primary save-profile" disabled={busy} type="submit">{busy ? "保存中…" : "保存配置"}</button>
        </form>
      </div>
    </section>
  </div>;
}

function effortLabel(effort: string): string {
  const labels: Record<string, string> = {
    none: "None · 关闭推理",
    minimal: "Minimal · 最少",
    low: "Low · 较低",
    medium: "Medium · 均衡",
    high: "High · 较高",
    xhigh: "XHigh · 很高",
    max: "Max · 最强",
  };
  return labels[effort] ?? effort;
}
