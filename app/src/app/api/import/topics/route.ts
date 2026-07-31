import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { readTopicLibrary, normalizeTitle } from "@/lib/sucai";

/**
 * POST /api/import/topics
 * 把素材库 选题库.csv（二次加工任务产出的选题）导入 Topic 表，接通"勾选 → 内容工坊"断点。
 *
 * 状态映射：
 *   "已勾选" / "勾选" / "approved"  → Topic.status = "approved"（用户已选定，准备扩写成稿）
 *   其余（"待勾选" 等）              → Topic.status = "draft"
 *
 * 去重：按归一化选题名跳过已存在的 Topic。
 * 关联：尝试按"归属板块/选题名"模糊匹配一条 xiaohongshu HotTopic 作为来源（匹配不到则留空）。
 */
export async function POST() {
  try {
    const libTopics = readTopicLibrary();
    if (libTopics.length === 0) {
      return NextResponse.json({ message: "选题库.csv 为空或不存在，未导入", count: 0, skipped: 0 });
    }

    const existing = await prisma.topic.findMany({ select: { title: true } });
    const seen = new Set(existing.map((t) => normalizeTitle(t.title)));

    // 拉一批小红书热点用于来源匹配
    const hotTopics = await prisma.hotTopic.findMany({
      where: { platform: "xiaohongshu" },
      select: { id: true, title: true, engagementScore: true },
      orderBy: { engagementScore: "desc" },
    });

    let created = 0;
    for (const lt of libTopics) {
      const name = lt.name?.trim();
      if (!name) continue;
      const nt = normalizeTitle(name);
      if (seen.has(nt)) continue;
      seen.add(nt);

      const approved = /已勾选|勾选|approved/i.test(lt.status ?? "") &&
        !/待勾选/.test(lt.status ?? "");

      // 简单来源匹配：选题名与热点标题有公共子串
      const match = hotTopics.find(
        (h) =>
          normalizeTitle(h.title).includes(nt.slice(0, 4)) ||
          nt.includes(normalizeTitle(h.title).slice(0, 4))
      );

      await prisma.topic.create({
        data: {
          title: name,
          angle: lt.board ? `板块：${lt.board}（来自选题库 ${lt.date ?? ""}）` : null,
          status: approved ? "approved" : "draft",
          hotTopicId: match?.id ?? null,
        },
      });
      created++;
    }

    return NextResponse.json({
      message: `选题导入完成，新增 ${created} 条，跳过重复 ${libTopics.length - created} 条`,
      count: created,
      skipped: libTopics.length - created,
      total: libTopics.length,
    });
  } catch (error) {
    console.error("[POST /api/import/topics]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}
