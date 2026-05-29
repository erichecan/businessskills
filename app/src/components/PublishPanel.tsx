"use client";

import { useState, useCallback, useEffect } from "react";
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

export default function PublishPanel({ onUpdate }: { onUpdate?: () => void }) {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const fetchReady = useCallback(async () => {
    setLoading(true);
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

  useEffect(() => { fetchReady(); }, [fetchReady]);

  async function copyItem(item: ContentItem) {
    const parts = [
      item.titleOptions ? `【标题选项】\n${item.titleOptions}` : null,
      item.finalDraft || item.draft,
    ].filter(Boolean).join("\n\n---\n\n");
    await navigator.clipboard.writeText(parts);
    setCopiedId(item.id);
    setTimeout(() => setCopiedId(null), 2000);
    onUpdate?.();
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button onClick={fetchReady} className="px-2.5 py-1 text-xs bg-zinc-800 text-zinc-400 rounded hover:text-white">刷新</button>
      </div>

      {loading ? <p className="text-zinc-500 text-sm">加载中…</p>
      : items.length === 0 ? (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-6 text-center">
          <p className="text-zinc-500 text-sm mb-1">暂无已就绪内容</p>
          <p className="text-zinc-600 text-xs">在内容工坊完成定稿后，状态为「已就绪」的内容会显示在这里</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(item => (
            <div key={item.id} className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
              <div className="flex items-start justify-between gap-3 mb-2">
                <div>
                  <p className="text-white font-medium text-sm">{item.topic.title}</p>
                  {item.topic.format && <span className="text-[10px] text-zinc-500">{item.topic.format}</span>}
                </div>
                <div className="flex gap-1.5 shrink-0">
                  <Link href={`/content/${item.id}`} className="px-2 py-1 text-xs bg-zinc-800 text-zinc-300 rounded hover:bg-zinc-700">查看详情</Link>
                  <button onClick={() => copyItem(item)} className="px-2 py-1 text-xs bg-white text-zinc-900 rounded font-medium hover:bg-zinc-100">
                    {copiedId === item.id ? "已复制 ✓" : "复制全文"}
                  </button>
                </div>
              </div>

              {item.titleOptions && (
                <div className="mb-2">
                  <p className="text-[10px] text-zinc-500 mb-1">标题选项</p>
                  <pre className="text-xs text-zinc-300 whitespace-pre-wrap bg-zinc-800 rounded p-2">{item.titleOptions}</pre>
                </div>
              )}

              <div>
                <p className="text-[10px] text-zinc-500 mb-1">正文</p>
                <pre className="text-xs text-zinc-300 whitespace-pre-wrap bg-zinc-800 rounded p-2 max-h-40 overflow-y-auto">
                  {item.finalDraft || item.draft || "（无内容）"}
                </pre>
              </div>

              <p className="text-[10px] text-zinc-600 mt-2">更新于 {new Date(item.updatedAt).toLocaleString("zh-CN")}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
