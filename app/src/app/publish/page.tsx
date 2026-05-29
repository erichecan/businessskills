"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";

type ContentItem = {
  id: string;
  finalDraft: string | null;
  draft: string | null;
  titleOptions: string | null;
  status: string;
  updatedAt: string;
  topic: { id: string; title: string; format: string | null };
};

export default function PublishPage() {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const fetchReady = useCallback(async () => {
    try {
      const res = await fetch("/api/content?status=ready");
      if (!res.ok) throw new Error("获取失败");
      setItems(await res.json());
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchReady();
  }, [fetchReady]);

  async function copyItem(item: ContentItem) {
    const parts = [
      item.titleOptions ? `【标题选项】\n${item.titleOptions}` : null,
      item.finalDraft || item.draft,
    ]
      .filter(Boolean)
      .join("\n\n---\n\n");
    await navigator.clipboard.writeText(parts);
    setCopiedId(item.id);
    setTimeout(() => setCopiedId(null), 2000);
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">发布准备</h1>
        <button
          onClick={fetchReady}
          className="px-3 py-1.5 text-xs bg-zinc-800 text-zinc-300 rounded hover:bg-zinc-700"
        >
          刷新
        </button>
      </div>

      {loading ? (
        <p className="text-zinc-500 text-sm">加载中…</p>
      ) : items.length === 0 ? (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-8 text-center">
          <p className="text-zinc-500 text-sm mb-2">暂无已就绪内容</p>
          <p className="text-zinc-600 text-xs">
            在内容工坊完成定稿后，状态变为「已就绪」的内容会显示在这里
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <div key={item.id} className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
              <div className="flex items-start justify-between gap-4 mb-3">
                <div>
                  <p className="text-white font-medium text-sm">{item.topic.title}</p>
                  {item.topic.format && (
                    <span className="text-xs text-zinc-500 mt-0.5 inline-block">
                      {item.topic.format}
                    </span>
                  )}
                </div>
                <div className="flex gap-2 shrink-0">
                  <Link
                    href={`/content/${item.id}`}
                    className="px-3 py-1.5 text-xs bg-zinc-800 text-zinc-300 rounded hover:bg-zinc-700"
                  >
                    查看详情
                  </Link>
                  <button
                    onClick={() => copyItem(item)}
                    className="px-3 py-1.5 text-xs bg-white text-zinc-900 rounded font-medium hover:bg-zinc-100"
                  >
                    {copiedId === item.id ? "已复制 ✓" : "复制全文"}
                  </button>
                </div>
              </div>

              {item.titleOptions && (
                <div className="mb-3">
                  <p className="text-xs text-zinc-500 mb-1 font-medium">标题选项</p>
                  <pre className="text-xs text-zinc-300 whitespace-pre-wrap bg-zinc-800 rounded p-2">
                    {item.titleOptions}
                  </pre>
                </div>
              )}

              <div>
                <p className="text-xs text-zinc-500 mb-1 font-medium">正文</p>
                <pre className="text-xs text-zinc-300 whitespace-pre-wrap bg-zinc-800 rounded p-2 max-h-48 overflow-y-auto">
                  {item.finalDraft || item.draft || "（无内容）"}
                </pre>
              </div>

              <p className="text-xs text-zinc-600 mt-3">
                更新于 {new Date(item.updatedAt).toLocaleString("zh-CN")}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
