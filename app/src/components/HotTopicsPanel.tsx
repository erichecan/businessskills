"use client";

import { useState, useEffect, useCallback } from "react";

type HotTopic = {
  id: string;
  platform: string;
  title: string;
  url: string | null;
  scrapedAt: string;
  status: string;
  _count: { topics: number };
};

const PLATFORM_LABELS: Record<string, string> = {
  manual: "手动", xiaohongshu: "小红书", weixin: "公众号", youtube: "YouTube",
};

const STATUS_LABELS: Record<string, { label: string; cls: string }> = {
  pending:    { label: "待判断", cls: "bg-zinc-700 text-zinc-300" },
  relevant:   { label: "相关",   cls: "bg-emerald-900 text-emerald-300" },
  irrelevant: { label: "无关",   cls: "bg-zinc-800 text-zinc-500" },
};

export default function HotTopicsPanel({ onUpdate }: { onUpdate?: () => void }) {
  const [topics, setTopics] = useState<HotTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [formTitle, setFormTitle] = useState("");
  const [formUrl, setFormUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    const url = filter === "all" ? "/api/hot-topics" : `/api/hot-topics?status=${filter}`;
    const res = await fetch(url);
    setTopics(await res.json());
    setLoading(false);
  }, [filter]);

  useEffect(() => { fetch_(); }, [fetch_]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!formTitle.trim()) return;
    setSubmitting(true); setError(null);
    try {
      const res = await fetch("/api/hot-topics", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: formTitle, url: formUrl || undefined }),
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.message || "失败"); }
      setFormTitle(""); setFormUrl("");
      fetch_(); onUpdate?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "失败");
    } finally { setSubmitting(false); }
  }

  async function updateStatus(id: string, status: string) {
    await fetch(`/api/hot-topics/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    setTopics(prev => prev.map(t => t.id === id ? { ...t, status } : t));
    onUpdate?.();
  }

  async function deleteTopic(id: string) {
    if (!confirm("确认删除？")) return;
    await fetch(`/api/hot-topics/${id}`, { method: "DELETE" });
    setTopics(prev => prev.filter(t => t.id !== id));
    onUpdate?.();
  }

  const displayed = filter === "all" ? topics : topics.filter(t => t.status === filter);

  return (
    <div className="space-y-4">
      {/* 录入表单 */}
      <form onSubmit={handleSubmit} className="flex gap-2 flex-wrap">
        <input
          type="text" placeholder="热点标题 *" value={formTitle}
          onChange={e => setFormTitle(e.target.value)}
          className="flex-1 min-w-36 bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
        />
        <input
          type="text" placeholder="链接（可选）" value={formUrl}
          onChange={e => setFormUrl(e.target.value)}
          className="flex-1 min-w-36 bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
        />
        <button
          type="submit" disabled={submitting || !formTitle.trim()}
          className="px-3 py-1.5 bg-white text-zinc-900 rounded text-sm font-medium hover:bg-zinc-100 disabled:opacity-40"
        >
          {submitting ? "录入中…" : "+ 录入"}
        </button>
      </form>
      {error && <p className="text-xs text-red-400">{error}</p>}

      {/* 筛选 */}
      <div className="flex gap-1.5">
        {["all", "pending", "relevant", "irrelevant"].map(s => (
          <button key={s} onClick={() => setFilter(s)}
            className={`px-2.5 py-1 rounded text-xs ${filter === s ? "bg-white text-zinc-900 font-medium" : "bg-zinc-800 text-zinc-400 hover:text-white"}`}
          >
            {s === "all" ? "全部" : STATUS_LABELS[s].label}
          </button>
        ))}
      </div>

      {/* 列表 */}
      {loading ? <p className="text-zinc-500 text-sm">加载中…</p>
      : displayed.length === 0 ? <p className="text-zinc-500 text-sm">暂无热点</p>
      : (
        <div className="space-y-2">
          {displayed.map(t => (
            <div key={t.id} className={`bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2.5 flex items-start gap-3 ${t.status === "irrelevant" ? "opacity-40" : ""}`}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5 flex-wrap">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">{PLATFORM_LABELS[t.platform] ?? t.platform}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_LABELS[t.status]?.cls}`}>{STATUS_LABELS[t.status]?.label}</span>
                  {t._count.topics > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900 text-blue-300">{t._count.topics} 个选题</span>}
                </div>
                <p className="text-white text-sm font-medium truncate">{t.title}</p>
                {t.url && <a href={t.url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-zinc-500 hover:text-zinc-300 truncate block">{t.url}</a>}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {t.status !== "relevant"   && <button onClick={() => updateStatus(t.id, "relevant")}   className="px-2 py-0.5 text-xs rounded bg-emerald-900 text-emerald-300 hover:bg-emerald-800">相关</button>}
                {t.status !== "irrelevant" && <button onClick={() => updateStatus(t.id, "irrelevant")} className="px-2 py-0.5 text-xs rounded bg-zinc-800 text-zinc-400 hover:bg-zinc-700">无关</button>}
                {t.status !== "pending"    && <button onClick={() => updateStatus(t.id, "pending")}    className="px-2 py-0.5 text-xs rounded bg-zinc-800 text-zinc-500 hover:bg-zinc-700">重置</button>}
                <button onClick={() => deleteTopic(t.id)} className="px-2 py-0.5 text-xs rounded bg-zinc-800 text-red-500 hover:bg-zinc-700">删</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
