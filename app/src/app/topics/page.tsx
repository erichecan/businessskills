"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";

type HotTopic = {
  id: string;
  title: string;
  platform: string;
};

type Topic = {
  id: string;
  title: string;
  angle: string | null;
  format: string | null;
  status: string;
  createdAt: string;
  hotTopic: HotTopic | null;
  _count: { contents: number };
};

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  draft: { label: "草稿", className: "bg-zinc-700 text-zinc-300" },
  approved: { label: "已批准", className: "bg-emerald-900 text-emerald-300" },
  rejected: { label: "已拒绝", className: "bg-red-900 text-red-400" },
};

const FORMAT_OPTIONS = ["图文", "短视频", "长视频"];

export default function TopicsPage() {
  const router = useRouter();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [relevantHotTopics, setRelevantHotTopics] = useState<HotTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState("all");
  const [showForm, setShowForm] = useState(false);
  const [formTitle, setFormTitle] = useState("");
  const [formAngle, setFormAngle] = useState("");
  const [formFormat, setFormFormat] = useState("");
  const [formHotTopicId, setFormHotTopicId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTopics = useCallback(async () => {
    try {
      const [topicsRes, hotTopicsRes] = await Promise.all([
        fetch("/api/topics"),
        fetch("/api/hot-topics?status=relevant"),
      ]);
      if (!topicsRes.ok || !hotTopicsRes.ok) throw new Error("获取数据失败");
      setTopics(await topicsRes.json());
      setRelevantHotTopics(await hotTopicsRes.json());
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTopics();
  }, [fetchTopics]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!formTitle.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/topics", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: formTitle,
          angle: formAngle || undefined,
          format: formFormat || undefined,
          hotTopicId: formHotTopicId || undefined,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.message || "创建失败");
      }
      setFormTitle("");
      setFormAngle("");
      setFormFormat("");
      setFormHotTopicId("");
      setShowForm(false);
      fetchTopics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function updateStatus(id: string, status: string) {
    try {
      const res = await fetch(`/api/topics/${id}`, {
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

  async function createContent(topicId: string) {
    try {
      const res = await fetch(`/api/topics/${topicId}/contents`, { method: "POST" });
      if (!res.ok) throw new Error("创建失败");
      const content = await res.json();
      router.push(`/content/${content.id}`);
    } catch (err) {
      console.error(err);
      alert("创建内容失败");
    }
  }

  const displayed =
    filterStatus === "all" ? topics : topics.filter((t) => t.status === filterStatus);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">选题</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-white text-zinc-900 rounded text-sm font-medium hover:bg-zinc-100"
        >
          {showForm ? "取消" : "+ 新建选题"}
        </button>
      </div>

      {showForm && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
          <h2 className="text-sm font-medium text-zinc-400 mb-3">新建选题</h2>
          <form onSubmit={handleSubmit} className="space-y-3">
            <input
              type="text"
              placeholder="选题标题 *"
              value={formTitle}
              onChange={(e) => setFormTitle(e.target.value)}
              className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
            />
            <input
              type="text"
              placeholder="切入角度（可选）"
              value={formAngle}
              onChange={(e) => setFormAngle(e.target.value)}
              className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
            />
            <div className="flex gap-3">
              <select
                value={formFormat}
                onChange={(e) => setFormFormat(e.target.value)}
                className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
              >
                <option value="">格式（可选）</option>
                {FORMAT_OPTIONS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
              <select
                value={formHotTopicId}
                onChange={(e) => setFormHotTopicId(e.target.value)}
                className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
              >
                <option value="">关联热点（可选）</option>
                {relevantHotTopics.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.title}
                  </option>
                ))}
              </select>
            </div>
            {error && <p className="text-sm text-red-400">{error}</p>}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="px-3 py-2 text-sm text-zinc-400 hover:text-white"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={submitting || !formTitle.trim()}
                className="px-4 py-2 bg-white text-zinc-900 rounded text-sm font-medium hover:bg-zinc-100 disabled:opacity-40"
              >
                {submitting ? "创建中…" : "创建"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 筛选 */}
      <div className="flex gap-2">
        {["all", "draft", "approved", "rejected"].map((s) => (
          <button
            key={s}
            onClick={() => setFilterStatus(s)}
            className={`px-3 py-1 rounded text-sm ${
              filterStatus === s
                ? "bg-white text-zinc-900 font-medium"
                : "bg-zinc-800 text-zinc-400 hover:text-white"
            }`}
          >
            {s === "all" ? "全部" : STATUS_CONFIG[s].label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-zinc-500 text-sm">加载中…</p>
      ) : displayed.length === 0 ? (
        <p className="text-zinc-500 text-sm">暂无选题</p>
      ) : (
        <div className="space-y-2">
          {displayed.map((topic) => (
            <div
              key={topic.id}
              className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 flex items-start gap-4"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${STATUS_CONFIG[topic.status]?.className}`}
                  >
                    {STATUS_CONFIG[topic.status]?.label}
                  </span>
                  {topic.format && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
                      {topic.format}
                    </span>
                  )}
                  {topic.hotTopic && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-blue-900 text-blue-300 truncate max-w-48">
                      热点: {topic.hotTopic.title}
                    </span>
                  )}
                  {topic._count.contents > 0 && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-violet-900 text-violet-300">
                      {topic._count.contents} 篇内容
                    </span>
                  )}
                </div>
                <p className="text-white text-sm font-medium">{topic.title}</p>
                {topic.angle && (
                  <p className="text-xs text-zinc-400 mt-0.5">角度: {topic.angle}</p>
                )}
                <p className="text-xs text-zinc-600 mt-1">
                  {new Date(topic.createdAt).toLocaleString("zh-CN")}
                </p>
              </div>
              <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
                <button
                  onClick={() => createContent(topic.id)}
                  className="px-2 py-1 text-xs rounded bg-violet-900 text-violet-300 hover:bg-violet-800"
                >
                  + 创建内容
                </button>
                {topic.status !== "approved" && (
                  <button
                    onClick={() => updateStatus(topic.id, "approved")}
                    className="px-2 py-1 text-xs rounded bg-emerald-900 text-emerald-300 hover:bg-emerald-800"
                  >
                    批准
                  </button>
                )}
                {topic.status !== "rejected" && (
                  <button
                    onClick={() => updateStatus(topic.id, "rejected")}
                    className="px-2 py-1 text-xs rounded bg-zinc-800 text-red-400 hover:bg-zinc-700"
                  >
                    拒绝
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
