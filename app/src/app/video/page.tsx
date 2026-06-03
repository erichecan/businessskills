"use client";

import { useState } from "react";

type Phase =
  | "topic" | "script-loading"
  | "script-review" | "board-review"
  | "images-gen" | "images-review"
  | "voice-gen" | "voice-review"
  | "video-gen" | "video-done";

const STEPS = [
  { num: "01", label: "话题", phases: ["topic", "script-loading"] },
  { num: "02", label: "口播稿", phases: ["script-review"] },
  { num: "03", label: "分镜描述", phases: ["board-review"] },
  { num: "04", label: "场景图", phases: ["images-gen", "images-review"] },
  { num: "05", label: "配音", phases: ["voice-gen", "voice-review"] },
  { num: "06", label: "合成视频", phases: ["video-gen", "video-done"] },
] as const;

function activeStepIndex(phase: Phase): number {
  return STEPS.findIndex(s => (s.phases as readonly string[]).includes(phase));
}

type ImgState = { index: number; url?: string; status: "pending" | "done" | "error" };

export default function VideoPage() {
  const [phase, setPhase] = useState<Phase>("topic");
  const [topic, setTopic] = useState("");
  const [episodeId, setEpisodeId] = useState("");
  const [script, setScript] = useState("");
  const [storyboard, setStoryboard] = useState<string[]>([]);
  const [images, setImages] = useState<ImgState[]>([]);
  const [genLog, setGenLog] = useState<string[]>([]);
  const [audioUrl, setAudioUrl] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [error, setError] = useState("");

  const active = activeStepIndex(phase);

  const reset = () => {
    setPhase("topic"); setTopic(""); setEpisodeId(""); setScript("");
    setStoryboard([]); setImages([]); setGenLog([]); setAudioUrl("");
    setVideoUrl(""); setError("");
  };

  const generateScript = async () => {
    if (!topic.trim()) return;
    setError(""); setPhase("script-loading");
    try {
      const res = await fetch("/api/video/script", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "生成失败");
      setEpisodeId(data.episodeId);
      setScript(data.script);
      setStoryboard(data.storyboard);
      setPhase("script-review");
    } catch (e) {
      setError(String(e)); setPhase("topic");
    }
  };

  const generateImages = async () => {
    setError("");
    setImages(storyboard.map((_, i) => ({ index: i + 1, status: "pending" })));
    setGenLog([]);
    setPhase("images-gen");
    try {
      const res = await fetch("/api/video/images", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ episodeId, storyboard }),
      });
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.type === "image") {
              setImages(prev => prev.map(img =>
                img.index === ev.index ? { ...img, url: ev.url + `?t=${Date.now()}`, status: "done" } : img
              ));
            } else if (ev.type === "error") {
              setImages(prev => prev.map(img =>
                img.index === ev.index ? { ...img, status: "error" } : img
              ));
            } else if (ev.type === "log" && ev.message) {
              setGenLog(prev => [...prev.slice(-40), ev.message]);
            } else if (ev.type === "done") {
              setPhase("images-review");
            }
          } catch { /* ignore parse errors */ }
        }
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const generateVoice = async () => {
    setError(""); setPhase("voice-gen");
    try {
      const res = await fetch("/api/video/voice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ episodeId, script }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "配音失败");
      setAudioUrl(data.audioUrl);
      setPhase("voice-review");
    } catch (e) {
      setError(String(e)); setPhase("images-review");
    }
  };

  const produceVideo = async () => {
    setError(""); setPhase("video-gen");
    try {
      const res = await fetch("/api/video/produce", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ episodeId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "合成失败");
      setVideoUrl(data.videoUrl);
      setPhase("video-done");
    } catch (e) {
      setError(String(e)); setPhase("voice-review");
    }
  };

  return (
    <div className="max-w-5xl mx-auto">
      <div className="mb-5">
        <h1 className="text-lg font-bold text-white">视频号生产流水线</h1>
        <p className="text-xs text-zinc-500 mt-0.5">话题 → 口播稿 → 分镜 → 场景图 → 配音 → 合成</p>
      </div>

      <div className="flex gap-4" style={{ minHeight: 540 }}>
        {/* Step sidebar */}
        <div className="flex flex-col gap-2 w-36 shrink-0">
          {STEPS.map((step, i) => {
            const isDone = i < active;
            const isActive = i === active;
            return (
              <div
                key={i}
                className={`rounded-lg border px-3 py-2.5 transition-all ${
                  isActive
                    ? "bg-zinc-800 border-zinc-700 ring-1 ring-white/20"
                    : isDone
                    ? "bg-zinc-900 border-zinc-800"
                    : "bg-zinc-900/40 border-zinc-800/40 opacity-40"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <span className={`text-[11px] font-black font-mono ${
                    isActive ? "text-white" : isDone ? "text-emerald-400" : "text-zinc-600"
                  }`}>
                    {step.num}
                  </span>
                  {isDone && <span className="text-[9px] text-emerald-400">✓</span>}
                </div>
                <div className="text-xs font-medium text-white mt-0.5">{step.label}</div>
              </div>
            );
          })}
          {phase !== "topic" && (
            <button
              onClick={reset}
              className="text-[10px] text-zinc-600 hover:text-zinc-400 mt-1 transition-colors"
            >
              + 新建视频
            </button>
          )}
        </div>

        {/* Content panel */}
        <div className={`flex-1 min-w-0 rounded-lg border p-5 ${
          active === 0 ? "bg-zinc-800 border-zinc-700" : "bg-zinc-900 border-zinc-800"
        }`}>
          {error && (
            <div className="mb-4 px-3 py-2 bg-red-950/80 border border-red-800 rounded text-xs text-red-300 leading-relaxed">
              {error}
            </div>
          )}

          {/* ── Step 1: Topic ── */}
          {(phase === "topic" || phase === "script-loading") && (
            <div>
              <h2 className="text-sm font-semibold text-white mb-1">输入话题</h2>
              <p className="text-xs text-zinc-500 mb-4">一句话描述今天视频的核心主题，AI 会自动生成口播稿和分镜</p>
              <textarea
                value={topic}
                onChange={e => setTopic(e.target.value)}
                disabled={phase === "script-loading"}
                placeholder="例：做了父亲之后，我才真正看懂了我爸"
                className="w-full bg-zinc-900 border border-zinc-700 rounded-lg p-3 text-sm text-white placeholder-zinc-600 resize-none focus:outline-none focus:ring-1 focus:ring-zinc-500 disabled:opacity-50"
                rows={3}
              />
              <div className="flex items-center gap-3 mt-3">
                <button
                  onClick={generateScript}
                  disabled={!topic.trim() || phase === "script-loading"}
                  className="px-4 py-2 bg-white text-zinc-900 text-sm font-medium rounded-lg hover:bg-zinc-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {phase === "script-loading" ? "生成中…" : "生成口播稿 + 分镜"}
                </button>
                {phase === "script-loading" && (
                  <span className="text-xs text-zinc-500 animate-pulse">Codex 正在创作，约 30 秒…</span>
                )}
              </div>
              {phase === "script-loading" && (
                <div className="mt-4 text-[10px] text-zinc-600 leading-relaxed">
                  正在调用 Codex CLI 生成口播稿（200-280字）和分镜描述（4-6个场景）
                </div>
              )}
            </div>
          )}

          {/* ── Step 2: Script review ── */}
          {phase === "script-review" && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <h2 className="text-sm font-semibold text-white">审核口播稿</h2>
                <span className="text-[10px] text-zinc-500">{script.length} 字</span>
              </div>
              <p className="text-xs text-zinc-500 mb-3">可直接在下方编辑，满意后点击确认进入下一步</p>
              <textarea
                value={script}
                onChange={e => setScript(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-700 rounded-lg p-3 text-sm text-zinc-200 leading-relaxed resize-none focus:outline-none focus:ring-1 focus:ring-zinc-500 font-mono"
                rows={14}
              />
              <div className="flex gap-2 mt-3">
                <button
                  onClick={() => setPhase("board-review")}
                  className="px-4 py-2 bg-white text-zinc-900 text-sm font-medium rounded-lg hover:bg-zinc-200 transition-colors"
                >
                  确认口播稿 →
                </button>
                <button
                  onClick={() => { setPhase("topic"); setScript(""); setStoryboard([]); }}
                  className="px-4 py-2 bg-zinc-700 text-zinc-200 text-sm rounded-lg hover:bg-zinc-600 transition-colors"
                >
                  重新生成
                </button>
              </div>
            </div>
          )}

          {/* ── Step 3: Storyboard review ── */}
          {phase === "board-review" && (
            <div>
              <h2 className="text-sm font-semibold text-white mb-1">审核分镜描述</h2>
              <p className="text-xs text-zinc-500 mb-3">
                每行是一个场景描述（英文），将用于生成对应场景图。可编辑、添加或删除。
              </p>
              <div className="flex flex-col gap-2">
                {storyboard.map((scene, i) => (
                  <div key={i} className="flex gap-2 items-start">
                    <span className="text-[10px] font-mono text-zinc-600 pt-2.5 w-6 shrink-0 text-right">
                      {i + 1}
                    </span>
                    <textarea
                      value={scene}
                      onChange={e => setStoryboard(prev => prev.map((s, j) => j === i ? e.target.value : s))}
                      className="flex-1 bg-zinc-950 border border-zinc-700 rounded-lg p-2.5 text-xs text-zinc-200 leading-relaxed resize-none focus:outline-none focus:ring-1 focus:ring-zinc-500 font-mono"
                      rows={2}
                    />
                    <button
                      onClick={() => setStoryboard(prev => prev.filter((_, j) => j !== i))}
                      className="text-zinc-600 hover:text-red-400 text-sm pt-2 transition-colors"
                      title="删除此场景"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
              <button
                onClick={() => setStoryboard(prev => [...prev, ""])}
                className="mt-2 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                + 添加场景
              </button>
              <div className="flex gap-2 mt-4">
                <button
                  onClick={generateImages}
                  disabled={storyboard.filter(s => s.trim()).length === 0}
                  className="px-4 py-2 bg-white text-zinc-900 text-sm font-medium rounded-lg hover:bg-zinc-200 disabled:opacity-40 transition-colors"
                >
                  确认分镜，生成场景图 →
                </button>
                <button
                  onClick={() => setPhase("script-review")}
                  className="px-4 py-2 bg-zinc-700 text-zinc-200 text-sm rounded-lg hover:bg-zinc-600 transition-colors"
                >
                  ← 返回口播稿
                </button>
              </div>
            </div>
          )}

          {/* ── Step 4: Image generation ── */}
          {(phase === "images-gen" || phase === "images-review") && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <h2 className="text-sm font-semibold text-white">场景图生成</h2>
                <span className="text-[10px] text-zinc-500">
                  {images.filter(i => i.status === "done").length} / {images.length} 完成
                </span>
              </div>
              {phase === "images-gen" && (
                <p className="text-xs text-zinc-500 mb-3 animate-pulse">
                  Codex + gpt-image-1 生成中，每张约 30 秒…
                </p>
              )}

              {/* Scene status list */}
              <div className="grid grid-cols-2 gap-3 mb-4">
                {images.map(img => (
                  <div
                    key={img.index}
                    className={`rounded-lg border overflow-hidden ${
                      img.status === "done"
                        ? "border-zinc-700"
                        : img.status === "error"
                        ? "border-red-800"
                        : "border-zinc-800"
                    }`}
                  >
                    {img.status === "done" && img.url ? (
                      <img
                        src={img.url}
                        alt={`场景 ${img.index}`}
                        className="w-full object-cover"
                        style={{ aspectRatio: "9/16", maxHeight: 200 }}
                      />
                    ) : (
                      <div
                        className={`flex items-center justify-center ${
                          img.status === "error" ? "bg-red-950/30" : "bg-zinc-900"
                        }`}
                        style={{ aspectRatio: "9/16", maxHeight: 200 }}
                      >
                        {img.status === "pending" && (
                          <span className="text-xs text-zinc-600">等待中</span>
                        )}
                        {img.status === "error" && (
                          <span className="text-xs text-red-500">生成失败</span>
                        )}
                        {phase === "images-gen" && img.status === "pending" &&
                          img.index === images.find(i => i.status === "pending")?.index && (
                          <span className="text-xs text-zinc-400 animate-pulse">生成中…</span>
                        )}
                      </div>
                    )}
                    <div className="px-2 py-1.5 bg-zinc-900">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] font-mono text-zinc-500">场景 {img.index}</span>
                        {img.status === "done" && <span className="text-[9px] text-emerald-400">✓</span>}
                        {img.status === "error" && <span className="text-[9px] text-red-400">✗</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {/* Log output */}
              {phase === "images-gen" && genLog.length > 0 && (
                <div className="bg-zinc-950 border border-zinc-800 rounded p-2 mb-3 max-h-24 overflow-y-auto">
                  {genLog.map((log, i) => (
                    <div key={i} className="text-[10px] font-mono text-zinc-500 leading-tight">{log}</div>
                  ))}
                </div>
              )}

              {phase === "images-review" && (
                <div className="flex gap-2">
                  <button
                    onClick={generateVoice}
                    className="px-4 py-2 bg-white text-zinc-900 text-sm font-medium rounded-lg hover:bg-zinc-200 transition-colors"
                  >
                    确认图片，生成配音 →
                  </button>
                  <button
                    onClick={() => setPhase("board-review")}
                    className="px-4 py-2 bg-zinc-700 text-zinc-200 text-sm rounded-lg hover:bg-zinc-600 transition-colors"
                  >
                    ← 修改分镜
                  </button>
                </div>
              )}
            </div>
          )}

          {/* ── Step 5: Voice ── */}
          {(phase === "voice-gen" || phase === "voice-review") && (
            <div>
              <h2 className="text-sm font-semibold text-white mb-1">配音生成</h2>
              {phase === "voice-gen" && (
                <p className="text-xs text-zinc-500 mb-4 animate-pulse">
                  调用 OpenAI TTS (onyx, 0.9x) 生成配音…
                </p>
              )}
              {phase === "voice-review" && audioUrl && (
                <div className="mb-4">
                  <p className="text-xs text-zinc-500 mb-3">试听配音效果，满意后合成视频</p>
                  <audio
                    controls
                    src={audioUrl}
                    className="w-full"
                    style={{ filter: "invert(1) hue-rotate(180deg)" }}
                  />
                </div>
              )}
              {phase === "voice-review" && (
                <div className="flex gap-2 mt-4">
                  <button
                    onClick={produceVideo}
                    className="px-4 py-2 bg-white text-zinc-900 text-sm font-medium rounded-lg hover:bg-zinc-200 transition-colors"
                  >
                    合成视频 →
                  </button>
                  <button
                    onClick={() => setPhase("images-review")}
                    className="px-4 py-2 bg-zinc-700 text-zinc-200 text-sm rounded-lg hover:bg-zinc-600 transition-colors"
                  >
                    ← 重新生成配音
                  </button>
                </div>
              )}
            </div>
          )}

          {/* ── Step 6: Video ── */}
          {(phase === "video-gen" || phase === "video-done") && (
            <div>
              <h2 className="text-sm font-semibold text-white mb-1">合成视频</h2>
              {phase === "video-gen" && (
                <p className="text-xs text-zinc-500 animate-pulse">
                  ffmpeg 合成中，通常需要 1-2 分钟…
                </p>
              )}
              {phase === "video-done" && videoUrl && (
                <div>
                  <p className="text-xs text-zinc-500 mb-3">视频已合成，预览确认后可下载发布</p>
                  <video
                    controls
                    src={videoUrl}
                    className="rounded-lg mx-auto"
                    style={{ maxHeight: 400 }}
                  />
                  <div className="mt-3">
                    <a
                      href={videoUrl}
                      download="video.mp4"
                      className="inline-block px-4 py-2 bg-emerald-700 text-white text-sm font-medium rounded-lg hover:bg-emerald-600 transition-colors"
                    >
                      下载 MP4
                    </a>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
