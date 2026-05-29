"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";

type HotTopic = { id: string; title: string; platform: string };
type Topic = {
  id: string; title: string; angle: string | null; format: string | null;
  status: string; createdAt: string;
  hotTopic: HotTopic | null; _count: { contents: number };
};

type Suggestion = {
  title: string;
  angle: string;
  format: string;
  viability: number;
  note: string;
};

type SuggestionResult = {
  related: Suggestion[];
  unrelated: Suggestion[];
};

const STATUS_CFG: Record<string, { label: string; cls: string }> = {
  draft:    { label: "草稿",   cls: "bg-zinc-700 text-zinc-300" },
  approved: { label: "已批准", cls: "bg-emerald-900 text-emerald-300" },
  rejected: { label: "已拒绝", cls: "bg-red-900 text-red-400" },
};
const FORMAT_OPTIONS = ["图文", "短视频", "长视频"];

function parseJsonFromResponse(text: string): SuggestionResult | null {
  // Try to extract JSON from code block
  const codeBlockMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const jsonStr = codeBlockMatch ? codeBlockMatch[1] : text;
  try {
    const parsed = JSON.parse(jsonStr.trim());
    if (parsed.related && parsed.unrelated) return parsed;
  } catch {
    // ignore
  }
  return null;
}

export default function TopicsPanel({ onUpdate }: { onUpdate?: () => void }) {
  const router = useRouter();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [hotTopics, setHotTopics] = useState<HotTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [showForm, setShowForm] = useState(false);
  const [formTitle, setFormTitle] = useState("");
  const [formAngle, setFormAngle] = useState("");
  const [formFormat, setFormFormat] = useState("");
  const [formHotTopicId, setFormHotTopicId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── AI 生成选题 ──
  const [showGenerator, setShowGenerator] = useState(false);
  const [genHotTopicId, setGenHotTopicId] = useState("");
  const [genPrompt, setGenPrompt] = useState<string | null>(null);
  const [genCopied, setGenCopied] = useState(false);
  const [genPaste, setGenPaste] = useState("");
  const [genResult, setGenResult] = useState<SuggestionResult | null>(null);
  const [genLoading, setGenLoading] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [genParseError, setGenParseError] = useState<string | null>(null);
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(new Set());
  const [creatingSelected, setCreatingSelected] = useState(false);

  const fetchAll = useCallback(async () => {
    const [tr, hr] = await Promise.all([fetch("/api/topics"), fetch("/api/hot-topics?status=relevant")]);
    setTopics(await tr.json());
    setHotTopics(await hr.json());
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!formTitle.trim()) return;
    setSubmitting(true); setError(null);
    try {
      const res = await fetch("/api/topics", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: formTitle, angle: formAngle || undefined, format: formFormat || undefined, hotTopicId: formHotTopicId || undefined }),
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.message || "失败"); }
      setFormTitle(""); setFormAngle(""); setFormFormat(""); setFormHotTopicId(""); setShowForm(false);
      fetchAll(); onUpdate?.();
    } catch (err) { setError(err instanceof Error ? err.message : "失败"); }
    finally { setSubmitting(false); }
  }

  async function updateStatus(id: string, status: string) {
    await fetch(`/api/topics/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    setTopics(prev => prev.map(t => t.id === id ? { ...t, status } : t));
    onUpdate?.();
  }

  async function createContent(topicId: string) {
    const res = await fetch(`/api/topics/${topicId}/contents`, { method: "POST" });
    if (!res.ok) { alert("创建失败"); return; }
    const content = await res.json();
    router.push(`/content/${content.id}`);
  }

  // ── Generator actions ──
  async function fetchGenPrompt() {
    if (!genHotTopicId) return;
    setGenLoading(true); setGenError(null); setGenPrompt(null); setGenResult(null); setGenPaste(""); setSelectedSuggestions(new Set());
    try {
      const res = await fetch(`/api/topics/generate-prompt?hotTopicId=${genHotTopicId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "获取失败");
      setGenPrompt(data.combined);
    } catch (err) {
      setGenError(err instanceof Error ? err.message : "获取失败");
    } finally {
      setGenLoading(false);
    }
  }

  async function copyGenPrompt() {
    if (!genPrompt) return;
    await navigator.clipboard.writeText(genPrompt);
    setGenCopied(true);
    setTimeout(() => setGenCopied(false), 2000);
  }

  function parseGenResult() {
    setGenParseError(null);
    const result = parseJsonFromResponse(genPaste);
    if (!result) {
      setGenParseError("无法解析 JSON，请确认 Claude 的回复包含正确格式的 JSON 代码块");
      return;
    }
    setGenResult(result);
  }

  function toggleSuggestion(key: string) {
    setSelectedSuggestions(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function createSelectedTopics() {
    if (!genResult) return;
    const all = [
      ...genResult.related.map((s, i) => ({ ...s, key: `r${i}` })),
      ...genResult.unrelated.map((s, i) => ({ ...s, key: `u${i}` })),
    ].filter(s => selectedSuggestions.has(s.key));

    if (all.length === 0) return;
    setCreatingSelected(true);
    try {
      for (const s of all) {
        await fetch("/api/topics", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: s.title,
            angle: s.angle || undefined,
            format: s.format || undefined,
            hotTopicId: genHotTopicId || undefined,
            status: "approved",
          }),
        });
      }
      fetchAll(); onUpdate?.();
      setGenResult(null); setGenPaste(""); setGenPrompt(null);
      setSelectedSuggestions(new Set()); setShowGenerator(false);
    } finally {
      setCreatingSelected(false);
    }
  }

  const displayed = filter === "all" ? topics : topics.filter(t => t.status === filter);

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex gap-1.5">
          {["all", "draft", "approved", "rejected"].map(s => (
            <button key={s} onClick={() => setFilter(s)}
              className={`px-2.5 py-1 rounded text-xs ${filter === s ? "bg-white text-zinc-900 font-medium" : "bg-zinc-800 text-zinc-400 hover:text-white"}`}
            >
              {s === "all" ? "全部" : STATUS_CFG[s].label}
            </button>
          ))}
        </div>
        <div className="flex gap-1.5">
          <button
            onClick={() => { setShowGenerator(f => !f); setShowForm(false); }}
            className={`px-3 py-1.5 rounded text-xs font-medium ${showGenerator ? "bg-amber-600 text-white" : "bg-amber-900 text-amber-300 hover:bg-amber-800"}`}
          >
            {showGenerator ? "关闭生成" : "✨ AI 生成选题"}
          </button>
          <button onClick={() => { setShowForm(f => !f); setShowGenerator(false); }}
            className="px-3 py-1.5 bg-white text-zinc-900 rounded text-xs font-medium hover:bg-zinc-100"
          >
            {showForm ? "取消" : "+ 手动新建"}
          </button>
        </div>
      </div>

      {/* AI Generator */}
      {showGenerator && (
        <div className="bg-zinc-900 border border-amber-900/50 rounded-lg p-3 space-y-3">
          <p className="text-xs text-amber-400 font-medium">✨ 从热点生成选题建议（中转 Claude）</p>

          {/* Step 1: select hot topic + get prompt */}
          <div className="flex gap-2">
            <select
              value={genHotTopicId}
              onChange={e => { setGenHotTopicId(e.target.value); setGenPrompt(null); setGenResult(null); setGenPaste(""); }}
              className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-zinc-500"
            >
              <option value="">选择相关热点…</option>
              {hotTopics.map(h => <option key={h.id} value={h.id}>{h.title}</option>)}
            </select>
            <button
              onClick={fetchGenPrompt}
              disabled={!genHotTopicId || genLoading}
              className="px-3 py-1.5 bg-zinc-700 text-zinc-200 rounded text-xs font-medium hover:bg-zinc-600 disabled:opacity-40 shrink-0"
            >
              {genLoading ? "生成中…" : "获取提示词"}
            </button>
          </div>

          {genError && <p className="text-xs text-red-400">{genError}</p>}

          {/* Step 2: copy prompt */}
          {genPrompt && !genResult && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs text-zinc-500">复制提示词 → 粘贴到 Claude → 将回复粘贴回来</p>
                <button
                  onClick={copyGenPrompt}
                  className="px-3 py-1 text-xs rounded bg-white text-zinc-900 font-medium hover:bg-zinc-100 shrink-0"
                >
                  {genCopied ? "已复制 ✓" : "复制提示词"}
                </button>
              </div>
              <pre className="text-xs text-zinc-500 bg-zinc-800 rounded p-2 max-h-24 overflow-y-auto whitespace-pre-wrap">
                {genPrompt.slice(0, 300)}…
              </pre>
              <textarea
                value={genPaste}
                onChange={e => setGenPaste(e.target.value)}
                rows={5}
                className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500 resize-y"
                placeholder="将 Claude 的回复粘贴到这里…"
              />
              {genParseError && <p className="text-xs text-red-400">{genParseError}</p>}
              <div className="flex justify-end">
                <button
                  onClick={parseGenResult}
                  disabled={!genPaste.trim()}
                  className="px-4 py-1.5 bg-white text-zinc-900 rounded text-xs font-medium hover:bg-zinc-100 disabled:opacity-40"
                >
                  解析结果
                </button>
              </div>
            </div>
          )}

          {/* Step 3: show suggestions */}
          {genResult && (
            <div className="space-y-3">
              <p className="text-xs text-zinc-400">选择想要创建的选题（已自动标记为「已批准」）：</p>

              {/* Related */}
              <div>
                <p className="text-[10px] text-emerald-400 font-medium mb-1.5">相关选题（{genResult.related.length} 个）</p>
                <div className="space-y-1">
                  {genResult.related.map((s, i) => {
                    const key = `r${i}`;
                    const selected = selectedSuggestions.has(key);
                    return (
                      <button
                        key={key}
                        onClick={() => toggleSuggestion(key)}
                        className={`w-full text-left rounded border px-2.5 py-2 transition-colors ${selected ? "border-emerald-600 bg-emerald-950" : "border-zinc-700 bg-zinc-800 hover:border-zinc-600"}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1 min-w-0">
                            <p className="text-xs text-white font-medium">{s.title}</p>
                            <p className="text-[10px] text-zinc-400 mt-0.5">{s.angle} · {s.format}</p>
                            <p className="text-[10px] text-zinc-500 mt-0.5">{s.note}</p>
                          </div>
                          <div className="flex items-center gap-1.5 shrink-0">
                            <span className="text-[10px] text-amber-400">{"★".repeat(s.viability)}{"☆".repeat(5 - s.viability)}</span>
                            <span className={`text-xs ${selected ? "text-emerald-400" : "text-zinc-600"}`}>{selected ? "✓" : "○"}</span>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Unrelated */}
              <div>
                <p className="text-[10px] text-zinc-500 font-medium mb-1.5">不相关选题（{genResult.unrelated.length} 个，仅供参考）</p>
                <div className="space-y-1">
                  {genResult.unrelated.map((s, i) => {
                    const key = `u${i}`;
                    const selected = selectedSuggestions.has(key);
                    return (
                      <button
                        key={key}
                        onClick={() => toggleSuggestion(key)}
                        className={`w-full text-left rounded border px-2.5 py-2 transition-colors opacity-70 ${selected ? "border-blue-600 bg-blue-950 opacity-100" : "border-zinc-700 bg-zinc-800 hover:border-zinc-600"}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1 min-w-0">
                            <p className="text-xs text-white font-medium">{s.title}</p>
                            <p className="text-[10px] text-zinc-400 mt-0.5">{s.angle} · {s.format}</p>
                            <p className="text-[10px] text-zinc-500 mt-0.5">{s.note}</p>
                          </div>
                          <div className="flex items-center gap-1.5 shrink-0">
                            <span className="text-[10px] text-amber-400">{"★".repeat(s.viability)}{"☆".repeat(5 - s.viability)}</span>
                            <span className={`text-xs ${selected ? "text-blue-400" : "text-zinc-600"}`}>{selected ? "✓" : "○"}</span>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="flex items-center justify-between pt-1">
                <p className="text-xs text-zinc-500">已选 {selectedSuggestions.size} 个</p>
                <div className="flex gap-2">
                  <button
                    onClick={() => { setGenResult(null); setGenPaste(""); setSelectedSuggestions(new Set()); }}
                    className="px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
                  >
                    重新生成
                  </button>
                  <button
                    onClick={createSelectedTopics}
                    disabled={selectedSuggestions.size === 0 || creatingSelected}
                    className="px-4 py-1.5 bg-white text-zinc-900 rounded text-xs font-medium hover:bg-zinc-100 disabled:opacity-40"
                  >
                    {creatingSelected ? "创建中…" : `创建选中 (${selectedSuggestions.size})`}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Manual Form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="bg-zinc-900 border border-zinc-700 rounded-lg p-3 space-y-2">
          <input type="text" placeholder="选题标题 *" value={formTitle} onChange={e => setFormTitle(e.target.value)}
            className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500" />
          <input type="text" placeholder="切入角度（可选）" value={formAngle} onChange={e => setFormAngle(e.target.value)}
            className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500" />
          <div className="flex gap-2">
            <select value={formFormat} onChange={e => setFormFormat(e.target.value)}
              className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-zinc-500">
              <option value="">格式（可选）</option>
              {FORMAT_OPTIONS.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
            <select value={formHotTopicId} onChange={e => setFormHotTopicId(e.target.value)}
              className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-zinc-500">
              <option value="">关联热点（可选）</option>
              {hotTopics.map(h => <option key={h.id} value={h.id}>{h.title}</option>)}
            </select>
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setShowForm(false)} className="px-3 py-1.5 text-xs text-zinc-400 hover:text-white">取消</button>
            <button type="submit" disabled={submitting || !formTitle.trim()}
              className="px-4 py-1.5 bg-white text-zinc-900 rounded text-xs font-medium hover:bg-zinc-100 disabled:opacity-40">
              {submitting ? "创建中…" : "创建"}
            </button>
          </div>
        </form>
      )}

      {/* List */}
      {loading ? <p className="text-zinc-500 text-sm">加载中…</p>
      : displayed.length === 0 ? <p className="text-zinc-500 text-sm">暂无选题</p>
      : (
        <div className="space-y-2">
          {displayed.map(t => (
            <div key={t.id} className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2.5 flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5 flex-wrap">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_CFG[t.status]?.cls}`}>{STATUS_CFG[t.status]?.label}</span>
                  {t.format && <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">{t.format}</span>}
                  {t.hotTopic && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900 text-blue-300 truncate max-w-40">热: {t.hotTopic.title}</span>}
                  {t._count.contents > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-900 text-violet-300">{t._count.contents} 篇内容</span>}
                </div>
                <p className="text-white text-sm font-medium">{t.title}</p>
                {t.angle && <p className="text-xs text-zinc-400 mt-0.5">角度: {t.angle}</p>}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {t.status === "approved" && (
                  <button onClick={() => createContent(t.id)} className="px-2 py-0.5 text-xs rounded bg-violet-900 text-violet-300 hover:bg-violet-800">+ 内容</button>
                )}
                {t.status !== "approved" && <button onClick={() => updateStatus(t.id, "approved")} className="px-2 py-0.5 text-xs rounded bg-emerald-900 text-emerald-300 hover:bg-emerald-800">批准</button>}
                {t.status !== "rejected" && <button onClick={() => updateStatus(t.id, "rejected")} className="px-2 py-0.5 text-xs rounded bg-zinc-800 text-red-400 hover:bg-zinc-700">拒绝</button>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
