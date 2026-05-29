"use client";

import { useState, useEffect, useCallback } from "react";
import HotTopicsPanel from "@/components/HotTopicsPanel";
import TopicsPanel from "@/components/TopicsPanel";
import PublishPanel from "@/components/PublishPanel";

type Stats = {
  hotTopicsTotal: number;
  hotTopicsRelevant: number;
  topicsTotal: number;
  topicsApproved: number;
  contentsTotal: number;
  contentsReady: number;
  contentsDrafting: number;
};

type Stage = {
  num: string;
  title: string;
  needsClaude: boolean;
  getStat: (s: Stats) => string;
  getStatus: (s: Stats) => "active" | "ok" | "blocked";
};

const STAGES: Stage[] = [
  {
    num: "01",
    title: "收集热点",
    needsClaude: false,
    getStat: (s) => s.hotTopicsTotal === 0 ? "暂无热点" : `${s.hotTopicsRelevant} / ${s.hotTopicsTotal} 条相关`,
    getStatus: (s) => s.hotTopicsRelevant > 0 ? "ok" : "active",
  },
  {
    num: "02",
    title: "确定选题",
    needsClaude: false,
    getStat: (s) => s.topicsTotal === 0 ? "暂无选题" : `${s.topicsApproved} / ${s.topicsTotal} 个已批准`,
    getStatus: (s) => {
      if (s.hotTopicsRelevant === 0) return "blocked";
      return s.topicsApproved > 0 ? "ok" : "active";
    },
  },
  {
    num: "03",
    title: "创作内容",
    needsClaude: true,
    getStat: (s) => s.contentsTotal === 0 ? "暂无内容" : s.contentsDrafting > 0 ? `${s.contentsDrafting} 篇创作中` : `${s.contentsTotal} 篇已创建`,
    getStatus: (s) => {
      if (s.topicsApproved === 0) return "blocked";
      return s.contentsTotal > 0 ? (s.contentsReady > 0 ? "ok" : "active") : "active";
    },
  },
  {
    num: "04",
    title: "准备发布",
    needsClaude: false,
    getStat: (s) => s.contentsReady === 0 ? "暂无就绪内容" : `${s.contentsReady} 篇待发布`,
    getStatus: (s) => {
      if (s.contentsReady > 0) return "ok";
      if (s.contentsTotal > 0) return "active";
      return "blocked";
    },
  },
];

const STATUS_STYLES = {
  active:  { dot: "bg-white",        num: "text-white",       row: "bg-zinc-800 border-zinc-700" },
  ok:      { dot: "bg-emerald-400",  num: "text-emerald-400", row: "bg-zinc-900 border-zinc-800" },
  blocked: { dot: "bg-zinc-700",     num: "text-zinc-600",    row: "bg-zinc-900/50 border-zinc-800/50" },
};

const STAGE_HINTS = [
  "手动输入热点标题（可附链接），标记为「相关」后进入下一步。",
  "从相关热点提炼选题，填写标题和角度，批准后进入下一步。点击已批准选题旁的「+ 内容」开始创作。",
  "对已批准的选题点「+ 内容」，进入内容工坊完成 5 步创作流程（需要中转 Claude 对话）。",
  "完成创作的内容会在此汇总，一键复制正文 + 标题，粘贴到小红书发布。",
];

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [active, setActive] = useState(0);

  const fetchStats = useCallback(async () => {
    const r = await fetch("/api/stats");
    setStats(await r.json());
  }, []);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  const getStatus = (i: number) => stats ? STAGES[i].getStatus(stats) : "blocked";

  // Auto-select the first non-ok stage
  useEffect(() => {
    if (!stats) return;
    const first = STAGES.findIndex((_, i) => getStatus(i) !== "ok");
    if (first !== -1) setActive(first);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stats]);

  const status = stats ? getStatus(active) : "blocked";
  const stage = STAGES[active];

  return (
    <div className="max-w-5xl mx-auto">
      <div className="mb-5">
        <h1 className="text-lg font-bold text-white">内容生产流程</h1>
        <p className="text-xs text-zinc-500 mt-0.5">4 步完成一篇小红书内容</p>
      </div>

      <div className="flex gap-4" style={{ minHeight: 520 }}>
        {/* ── Left: Step Nav ── */}
        <div className="flex flex-col gap-2 w-40 shrink-0">
          {STAGES.map((st, i) => {
            const s = stats ? getStatus(i) : "blocked";
            const styles = STATUS_STYLES[s];
            const isActive = i === active;
            return (
              <button
                key={i}
                onClick={() => setActive(i)}
                className={`text-left rounded-lg border px-3 py-3 transition-all ${styles.row} ${isActive ? "ring-1 ring-white/20" : "opacity-70 hover:opacity-100"}`}
              >
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-black font-mono ${styles.num}`}>{st.num}</span>
                  {s === "ok" && <span className="text-[10px] text-emerald-400">✓</span>}
                </div>
                <div className="text-xs font-medium text-white mt-1">{st.title}</div>
                {stats && (
                  <div className="text-[10px] text-zinc-500 mt-0.5 leading-tight">{st.getStat(stats)}</div>
                )}
                {st.needsClaude && (
                  <div className="text-[10px] text-amber-500/80 mt-1">↔ 需要 Claude</div>
                )}
              </button>
            );
          })}
        </div>

        {/* ── Right: Inline Panel ── */}
        <div className={`flex-1 min-w-0 rounded-lg border p-4 ${STATUS_STYLES[status].row}`}>
          {/* Header */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className={`text-2xl font-black font-mono ${STATUS_STYLES[status].num}`}>{stage.num}</span>
              <span className="text-base font-semibold text-white">{stage.title}</span>
              {status === "ok" && <span className="text-xs text-emerald-400">✓ 已完成</span>}
            </div>
            <div className="flex items-center gap-2">
              {stage.needsClaude
                ? <span className="text-[10px] bg-amber-950 text-amber-400 border border-amber-800 px-2 py-0.5 rounded">↔ 需要中转 Claude</span>
                : <span className="text-[10px] bg-zinc-800 text-zinc-400 border border-zinc-700 px-2 py-0.5 rounded">✏️ 在 App 内操作</span>
              }
            </div>
          </div>

          {/* Hint */}
          <p className="text-xs text-zinc-400 mb-3 leading-relaxed">{STAGE_HINTS[active]}</p>

          {/* Blocked state */}
          {status === "blocked" ? (
            <div className="flex items-center justify-center h-32">
              <p className="text-sm text-zinc-600">请先完成上一步</p>
            </div>
          ) : (
            <>
              {active === 0 && <HotTopicsPanel onUpdate={fetchStats} />}
              {active === 1 && <TopicsPanel onUpdate={fetchStats} />}
              {active === 2 && <TopicsPanel onUpdate={fetchStats} />}
              {active === 3 && <PublishPanel onUpdate={fetchStats} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
