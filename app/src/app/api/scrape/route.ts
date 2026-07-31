import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { readMemoryNotes, normalizeTitle } from "@/lib/sucai";

// POST /api/scrape — trigger scraping / import for a platform
// Body: { platform: "weixin", accountId: "..." }
//   or: { platform: "xiaohongshu" }  ← 从素材库 记忆库.csv 增量入库
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { platform, accountId } = body;

    if (platform === "weixin") {
      return await scrapeWeixin(accountId);
    }

    if (platform === "xiaohongshu") {
      return await importXiaohongshu();
    }

    return NextResponse.json(
      { error: true, message: `平台 ${platform} 暂不支持抓取` },
      { status: 400 }
    );
  } catch (error) {
    console.error("[POST /api/scrape]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}

async function scrapeWeixin(accountId: string) {
  if (!accountId?.trim()) {
    return NextResponse.json(
      { error: true, message: "请提供公众号 ID" },
      { status: 400 }
    );
  }

  const rsshubBase = (process.env.RSSHUB_BASE_URL ?? "https://rsshub.app").replace(/\/$/, "");
  const feedUrl = `${rsshubBase}/wechat/mp/article/${accountId}`;

  let xml: string;
  try {
    const res = await fetch(feedUrl, {
      headers: { "User-Agent": "Mozilla/5.0 (compatible; ContentPipeline/1.0)" },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) throw new Error(`RSSHub 返回 ${res.status}`);
    xml = await res.text();
  } catch (err) {
    return NextResponse.json(
      { error: true, message: `抓取失败: ${err instanceof Error ? err.message : String(err)}` },
      { status: 502 }
    );
  }

  // Parse RSS XML — lightweight regex parse (no xml parser needed for simple RSS)
  const items = parseRssItems(xml);
  if (items.length === 0) {
    return NextResponse.json({ message: "未解析到文章", count: 0 });
  }

  const created = await Promise.all(
    items.map((item) =>
      prisma.hotTopic.create({
        data: {
          platform: "weixin",
          title: item.title,
          url: item.link || null,
          rawData: JSON.stringify(item),
        },
      })
    )
  );

  return NextResponse.json({ message: `抓取成功，新增 ${created.length} 条热点`, count: created.length });
}

type RssItem = { title: string; link: string; pubDate: string };

function parseRssItems(xml: string): RssItem[] {
  const items: RssItem[] = [];
  const itemRegex = /<item>([\s\S]*?)<\/item>/g;
  let match;
  while ((match = itemRegex.exec(xml)) !== null) {
    const block = match[1];
    const title = extractTag(block, "title") ?? "";
    const link = extractTag(block, "link") ?? extractTag(block, "guid") ?? "";
    const pubDate = extractTag(block, "pubDate") ?? "";
    if (title) items.push({ title, link, pubDate });
  }
  return items;
}

function extractTag(xml: string, tag: string): string | null {
  const m = xml.match(new RegExp(`<${tag}[^>]*>(?:<!\\[CDATA\\[)?(.*?)(?:\\]\\]>)?<\\/${tag}>`, "s"));
  return m ? m[1].trim() : null;
}

/**
 * 小红书入库：读取素材库 职场面试_记忆库.csv，把定时采集任务的产物增量写进 HotTopic。
 * - platform = "xiaohongshu"
 * - engagementScore = 热度数值化（37万 → 370000）
 * - rawData = 原始行 JSON（关键词/来源/链接/热度原始值/归一化标题，便于后续去重与复看）
 * 去重：已存在的 xiaohongshu 热点按 url 优先、归一化标题兜底，跳过不重复入库。
 */
async function importXiaohongshu() {
  let notes;
  try {
    notes = readMemoryNotes();
  } catch (err) {
    return NextResponse.json(
      { error: true, message: `读取素材库失败: ${err instanceof Error ? err.message : String(err)}` },
      { status: 502 }
    );
  }

  if (notes.length === 0) {
    return NextResponse.json({ message: "素材库 记忆库.csv 为空或不存在，未导入", count: 0, skipped: 0 });
  }

  // 已有 xiaohongshu 热点，建立去重索引（url + 归一化标题）
  const existing = await prisma.hotTopic.findMany({
    where: { platform: "xiaohongshu" },
    select: { url: true, title: true, rawData: true },
  });
  const seenUrl = new Set<string>();
  const seenTitle = new Set<string>();
  for (const e of existing) {
    if (e.url) seenUrl.add(e.url);
    seenTitle.add(normalizeTitle(e.title));
  }

  const toCreate = notes.filter((n) => {
    const nt = normalizeTitle(n.title);
    if (n.url && seenUrl.has(n.url)) return false;
    if (seenTitle.has(nt)) return false;
    // 同批内部也去重
    if (n.url) seenUrl.add(n.url);
    seenTitle.add(nt);
    return n.title.trim() !== "";
  });

  let created = 0;
  for (const n of toCreate) {
    await prisma.hotTopic.create({
      data: {
        platform: "xiaohongshu",
        title: n.title,
        url: n.url,
        engagementScore: n.heatNum,
        rawData: JSON.stringify({
          keyword: n.keyword,
          source: n.source,
          firstSeen: n.firstSeen,
          heatRaw: n.heatRaw,
          normalizedTitle: normalizeTitle(n.title),
        }),
      },
    });
    created++;
  }

  return NextResponse.json({
    message: `小红书素材入库完成，新增 ${created} 条，跳过重复 ${notes.length - created} 条`,
    count: created,
    skipped: notes.length - created,
    total: notes.length,
  });
}
