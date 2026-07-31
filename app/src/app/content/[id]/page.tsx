"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";

// ── Progress Bar ─────────────────────────────────────────────────────────────
type StepDef = {
  label: string;
  relay: boolean; // true = 需要中转到 Claude
  done: (c: Content) => boolean;
};

const STEPS: StepDef[] = [
  { label: "生成初稿", relay: true,  done: (c) => !!c.draft },
  { label: "定稿",     relay: false, done: (c) => !!c.finalDraft },
  { label: "内容诊断", relay: true,  done: (c) => !!c.diagnosisResult },
  { label: "AI 检测",  relay: true,  done: (c) => !!c.aiCheckResult },
  { label: "生成标题", relay: true,  done: (c) => !!c.titleOptions },
];

function getActiveStep(c: Content): number {
  for (let i = 0; i < STEPS.length; i++) {
    if (!STEPS[i].done(c)) return i;
  }
  return STEPS.length; // all done
}

function StepProgressBar({ content }: { content: Content }) {
  const active = getActiveStep(content);
  const allDone = active === STEPS.length;

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        {STEPS.map((step, i) => {
          const done = step.done(content);
          const isCurrent = i === active;
          return (
            <div key={i} className="flex items-center gap-1 shrink-0">
              {/* node */}
              <div className={`flex flex-col items-center gap-1`}>
                <div className={`
                  w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border
                  ${done
                    ? "bg-emerald-800 border-emerald-600 text-emerald-300"
                    : isCurrent
                      ? "bg-white border-white text-zinc-900"
                      : "bg-zinc-800 border-zinc-700 text-zinc-500"}
                `}>
                  {done ? "✓" : i + 1}
                </div>
                <div className="flex flex-col items-center">
                  <span className={`text-xs whitespace-nowrap ${done ? "text-emerald-400" : isCurrent ? "text-white font-medium" : "text-zinc-500"}`}>
                    {step.label}
                  </span>
                  {step.relay && (
                    <span className={`text-[10px] whitespace-nowrap ${done ? "text-zinc-600" : isCurrent ? "text-amber-400" : "text-zinc-600"}`}>
                      ↔ 中转 Claude
                    </span>
                  )}
                </div>
              </div>
              {/* connector */}
              {i < STEPS.length - 1 && (
                <div className={`w-6 h-px mt-[-18px] ${done ? "bg-emerald-700" : "bg-zinc-700"}`} />
              )}
            </div>
          );
        })}
        {allDone && (
          <div className="ml-2 text-xs text-emerald-400 font-medium shrink-0 mt-[-18px]">🎉 全部完成</div>
        )}
      </div>
      {!allDone && active < STEPS.length && STEPS[active].relay && (
        <p className="text-xs text-amber-400/80 mt-3 border-t border-zinc-800 pt-2">
          当前步骤需要中转：点击「获取提示词」→ 复制到 Claude 对话框 → 将回复粘贴回来保存
        </p>
      )}
      {!allDone && active < STEPS.length && !STEPS[active].relay && (
        <p className="text-xs text-blue-400/80 mt-3 border-t border-zinc-800 pt-2">
          当前步骤在 App 内完成：直接编辑并保存定稿
        </p>
      )}
    </div>
  );
}

type Content = {
  id: string;
  diagnosisResult: string | null;
  draft: string | null;
  aiCheckResult: string | null;
  finalDraft: string | null;
  titleOptions: string | null;
  status: string;
  topic: {
    title: string;
    angle: string | null;
    format: string | null;
    hotTopic: { title: string; platform: string } | null;
  };
};

export default function ContentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [content, setContent] = useState<Content | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  const fetchContent = useCallback(async () => {
    const res = await fetch(`/api/content/${id}`);
    if (res.status === 404) { setNotFound(true); setLoading(false); return; }
    setContent(await res.json());
    setLoading(false);
  }, [id]);

  useEffect(() => { fetchContent(); }, [fetchContent]);

  if (loading) return <p className="text-zinc-500 text-sm p-6">加载中…</p>;
  if (notFound || !content) return <p className="text-zinc-500 text-sm p-6">内容不存在</p>;

  const statusLabel: Record<string, string> = { drafting: "创作中", reviewing: "审核中", ready: "已就绪" };
  const statusClass: Record<string, string> = {
    drafting: "bg-zinc-700 text-zinc-300",
    reviewing: "bg-amber-900 text-amber-300",
    ready: "bg-emerald-900 text-emerald-300",
  };

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <div className="flex items-start gap-3">
        <button onClick={() => router.back()} className="text-zinc-500 hover:text-white text-sm mt-1">← 返回</button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-xs px-1.5 py-0.5 rounded ${statusClass[content.status] ?? "bg-zinc-700 text-zinc-300"}`}>
              {statusLabel[content.status] ?? content.status}
            </span>
          </div>
          <h1 className="text-xl font-bold text-white">{content.topic.title}</h1>
          {content.topic.angle && <p className="text-sm text-zinc-400 mt-0.5">角度：{content.topic.angle}</p>}
        </div>
      </div>

      <StepProgressBar content={content} />

      <RelayStep
        title="① 生成初稿"
        promptEndpoint={`/api/content/${id}/draft`}
        saveEndpoint={`/api/content/${id}/draft`}
        savedResult={content.draft}
        onSaved={(updated) => setContent((c) => c ? { ...c, draft: updated.draft, status: updated.status } : c)}
        hint="把提示词粘贴到 Claude 对话框，将生成的正文粘回这里"
      />

      <FinalDraftSection
        id={id}
        initialValue={content.finalDraft || content.draft || ""}
        onSaved={(updated) => setContent((c) => c ? { ...c, finalDraft: updated.finalDraft, status: updated.status } : c)}
      />

      <RelayStep
        title="③ 内容诊断"
        promptEndpoint={`/api/content/${id}/diagnose`}
        saveEndpoint={`/api/content/${id}/diagnose`}
        savedResult={content.diagnosisResult}
        onSaved={(updated) => setContent((c) => c ? { ...c, diagnosisResult: updated.diagnosisResult } : c)}
        hint="把提示词粘贴到 Claude 对话框，将五维诊断结果粘回这里"
      />

      <RelayStep
        title="④ AI 痕迹检测"
        promptEndpoint={`/api/content/${id}/ai-check`}
        saveEndpoint={`/api/content/${id}/ai-check`}
        savedResult={content.aiCheckResult}
        onSaved={(updated) => setContent((c) => c ? { ...c, aiCheckResult: updated.aiCheckResult } : c)}
        hint="把提示词粘贴到 Claude 对话框，将检测结果粘回这里"
      />

      <RelayStep
        title="⑤ 生成标题"
        promptEndpoint={`/api/content/${id}/title`}
        saveEndpoint={`/api/content/${id}/title`}
        savedResult={content.titleOptions}
        onSaved={(updated) => setContent((c) => c ? { ...c, titleOptions: updated.titleOptions } : c)}
        hint="把提示词粘贴到 Claude 对话框，将标题选项粘回这里"
      />
    </div>
  );
}

// ── Relay Step ──────────────────────────────────────────────────────────────
function RelayStep({
  title,
  promptEndpoint,
  saveEndpoint,
  savedResult,
  onSaved,
  hint,
}: {
  title: string;
  promptEndpoint: string;
  saveEndpoint: string;
  savedResult: string | null;
  onSaved: (data: Record<string, string>) => void;
  hint: string;
}) {
  const [promptText, setPromptText] = useState<string | null>(null);
  const [loadingPrompt, setLoadingPrompt] = useState(false);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [pasteValue, setPasteValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [showPaste, setShowPaste] = useState(false);

  async function loadPrompt() {
    setLoadingPrompt(true);
    setPromptError(null);
    try {
      const res = await fetch(promptEndpoint);
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "获取提示词失败");
      setPromptText(data.combined);
      setShowPaste(true);
    } catch (err) {
      setPromptError(err instanceof Error ? err.message : "获取失败");
    } finally {
      setLoadingPrompt(false);
    }
  }

  async function copyPrompt() {
    if (!promptText) return;
    await navigator.clipboard.writeText(promptText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function saveResult() {
    if (!pasteValue.trim()) return;
    setSaving(true);
    try {
      const res = await fetch(saveEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ result: pasteValue }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "保存失败");
      onSaved(data);
      setShowPaste(false);
      setPasteValue("");
      setPromptText(null);
    } catch (err) {
      alert(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        <div className="flex gap-2">
          {savedResult && !showPaste && (
            <button
              onClick={() => setShowPaste(true)}
              className="px-2 py-1 text-xs rounded bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
            >
              重新录入
            </button>
          )}
          <button
            onClick={loadPrompt}
            disabled={loadingPrompt}
            className="px-3 py-1 text-xs rounded bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-40"
          >
            {loadingPrompt ? "生成中…" : "获取提示词"}
          </button>
        </div>
      </div>

      {promptError && <p className="text-xs text-red-400">{promptError}</p>}

      {promptText && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs text-zinc-500">{hint}</p>
            <button
              onClick={copyPrompt}
              className="px-3 py-1 text-xs rounded bg-white text-zinc-900 font-medium hover:bg-zinc-100 shrink-0"
            >
              {copied ? "已复制 ✓" : "复制提示词"}
            </button>
          </div>
          <pre className="text-xs text-zinc-400 bg-zinc-800 rounded p-3 max-h-36 overflow-y-auto whitespace-pre-wrap">
            {promptText}
          </pre>
        </div>
      )}

      {showPaste && (
        <div className="space-y-2">
          <p className="text-xs text-zinc-500">将 Claude 的回复粘贴到下方：</p>
          <textarea
            value={pasteValue}
            onChange={(e) => setPasteValue(e.target.value)}
            rows={6}
            className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500 resize-y"
            placeholder="粘贴 Claude 回复内容…"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => { setShowPaste(false); setPasteValue(""); }}
              className="px-3 py-1.5 text-sm text-zinc-400 hover:text-white"
            >
              取消
            </button>
            <button
              onClick={saveResult}
              disabled={saving || !pasteValue.trim()}
              className="px-4 py-1.5 bg-white text-zinc-900 rounded text-sm font-medium hover:bg-zinc-100 disabled:opacity-40"
            >
              {saving ? "保存中…" : "保存结果"}
            </button>
          </div>
        </div>
      )}

      {savedResult && !showPaste && (
        <pre className="text-xs text-zinc-300 whitespace-pre-wrap bg-zinc-800 rounded p-3 max-h-48 overflow-y-auto">
          {savedResult}
        </pre>
      )}
    </div>
  );
}

// ── Final Draft Section ─────────────────────────────────────────────────────
function FinalDraftSection({
  id,
  initialValue,
  onSaved,
}: {
  id: string;
  initialValue: string;
  onSaved: (data: Record<string, string>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(initialValue);
  const [saving, setSaving] = useState(false);

  useEffect(() => { setText(initialValue); }, [initialValue]);

  async function save() {
    setSaving(true);
    try {
      const res = await fetch(`/api/content/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ finalDraft: text, status: "ready" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "保存失败");
      onSaved(data);
      setEditing(false);
    } catch (err) {
      alert(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">② 定稿</h2>
        {!editing && (
          <button
            onClick={() => setEditing(true)}
            className="px-2 py-1 text-xs rounded bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
          >
            编辑
          </button>
        )}
      </div>
      {editing ? (
        <div className="space-y-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={10}
            className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500 resize-y"
            placeholder="在此编辑定稿内容…"
          />
          <div className="flex justify-end gap-2">
            <button onClick={() => setEditing(false)} className="px-3 py-1.5 text-sm text-zinc-400 hover:text-white">取消</button>
            <button
              onClick={save}
              disabled={saving}
              className="px-4 py-1.5 bg-white text-zinc-900 rounded text-sm font-medium hover:bg-zinc-100 disabled:opacity-40"
            >
              {saving ? "保存中…" : "保存定稿（标记已就绪）"}
            </button>
          </div>
        </div>
      ) : (
        <pre className="text-xs text-zinc-300 whitespace-pre-wrap min-h-10">
          {text || <span className="text-zinc-600">先完成初稿步骤，再编辑定稿</span>}
        </pre>
      )}
    </div>
  );
}
