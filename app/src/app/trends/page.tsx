"use client";

import { useState, useEffect, useCallback } from "react";

type HotTopic = {
  id: string;
  platform: string;
  title: string;
  url: string | null;
  engagementScore: number | null;
  scrapedAt: string;
  status: string;
  _count: { topics: number };
};

const PLATFORM_LABELS: Record<string, string> = {
  manual: "手动",
  xiaohongshu: "小红书",
  weixin: "公众号",
  youtube: "YouTube",
};

const STATUS_LABELS: Record<string, { label: string; className: string }> = {
  pending: { label: "待判断", className: "bg-zinc-700 text-zinc-300" },
  relevant: { label: "相关", className: "bg-emerald-900 text-emerald-300" },
  irrelevant: { label: "无关", className: "bg-zinc-800 text-zinc-500" },
};

export default function TrendsPage() {
  const [topics, setTopics] = useState<HotTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [formTitle, setFormTitle] = useState("");
  const [formUrl, setFormUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTopics = useCallback(async () => {
    try {
      const url = filterStatus === "all" ? "/api/hot-topics" : `/api/hot-topics?status=${filterStatus}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("获取热点失败");
      setTopics(await res.json());
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [filterStatus]);

  useEffect(() => {
    fetchTopics();
  }, [fetchTopics]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!formTitle.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/hot-topics", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: formTitle, url: formUrl || undefined }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.message || "创建失败");
      }
      setFormTitle("");
      setFormUrl("");
      fetchTopics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function updateStatus(id: string, status: string) {
    try {
      const res = await fetch(`/api/hot-topics/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) throw new Error("更新失败");
      setTopics((prev) => prev.map((t) => (t.id === id ? { ...t, status } : t)));
    } catch (err) {
      console.error(err);
    }
  }

  async function deleteTopic(id: string) {
    if (!confirm("确认删除？")) return;
    try {
      await fetch(`/api/hot-topics/${id}`, { method: "DELETE" });
      setTopics((prev) => prev.filter((t) => t.id !== id));
    } catch (err) {
      console.error(err);
    }
  }

  const displayed = filterStatus === "all" ? topics : topics.filter((t) => t.status === filterStatus);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-white">热点</h1>

      {/* 手动输入 */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <h2 className="text-sm font-medium text-zinc-400 mb-3">手动录入热点</h2>
        <form onSubmit={handleSubmit} className="flex gap-3 flex-wrap">
          <input
            type="text"
            placeholder="热点标题 *"
            value={formTitle}
            onChange={(e) => setFormTitle(e.target.value)}
            className="flex-1 min-w-48 bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
          />
          <input
            type="text"
            placeholder="原文链接（可选）"
            value={formUrl}
            onChange={(e) => setFormUrl(e.target.value)}
            className="flex-1 min-w-48 bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
          />
          <button
            type="submit"
            disabled={submitting || !formTitle.trim()}
            className="px-4 py-2 bg-white text-zinc-900 rounded text-sm font-medium hover:bg-zinc-100 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? "录入中…" : "录入"}
          </button>
        </form>
        {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
      </div>

      {/* 筛选 */}
      <div className="flex gap-2">
        {["all", "pending", "relevant", "irrelevant"].map((s) => (
          <button
            key={s}
            onClick={() => setFilterStatus(s)}
            className={`px-3 py-1 rounded text-sm ${
              filterStatus === s
                ? "bg-white text-zinc-900 font-medium"
                : "bg-zinc-800 text-zinc-400 hover:text-white"
            }`}
          >
            {s === "all" ? "全部" : STATUS_LABELS[s].label}
          </button>
        ))}
      </div>

      {/* 列表 */}
      {loading ? (
        <p className="text-zinc-500 text-sm">加载中…</p>
      ) : displayed.length === 0 ? (
        <p className="text-zinc-500 text-sm">暂无热点，手动录入或触发抓取</p>
      ) : (
        <div className="space-y-2">
          {displayed.map((topic) => (
            <div
              key={topic.id}
              className={`bg-zinc-900 border rounded-lg p-4 flex items-start gap-4 ${
                topic.status === "irrelevant" ? "border-zinc-800 opacity-50" : "border-zinc-800"
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
                    {PLATFORM_LABELS[topic.platform] ?? topic.platform}
                  </span>
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${STATUS_LABELS[topic.status]?.className}`}
                  >
                    {STATUS_LABELS[topic.status]?.label}
                  </span>
                  {topic._count.topics > 0 && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-blue-900 text-blue-300">
                      {topic._count.topics} 个选题
                    </span>
                  )}
                </div>
                <p className="text-white text-sm font-medium truncate">{topic.title}</p>
                {topic.url && (
                  <a
                    href={topic.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-zinc-500 hover:text-zinc-300 truncate block mt-0.5"
                  >
                    {topic.url}
                  </a>
                )}
                <p className="text-xs text-zinc-600 mt-1">
                  {new Date(topic.scrapedAt).toLocaleString("zh-CN")}
                </p>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {topic.status !== "relevant" && (
                  <button
                    onClick={() => updateStatus(topic.id, "relevant")}
                    className="px-2 py-1 text-xs rounded bg-emerald-900 text-emerald-300 hover:bg-emerald-800"
                  >
                    相关
                  </button>
                )}
                {topic.status !== "irrelevant" && (
                  <button
                    onClick={() => updateStatus(topic.id, "irrelevant")}
                    className="px-2 py-1 text-xs rounded bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
                  >
                    无关
                  </button>
                )}
                {topic.status !== "pending" && (
                  <button
                    onClick={() => updateStatus(topic.id, "pending")}
                    className="px-2 py-1 text-xs rounded bg-zinc-800 text-zinc-500 hover:bg-zinc-700"
                  >
                    重置
                  </button>
                )}
                <button
                  onClick={() => deleteTopic(topic.id)}
                  className="px-2 py-1 text-xs rounded bg-zinc-800 text-red-500 hover:bg-zinc-700"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
