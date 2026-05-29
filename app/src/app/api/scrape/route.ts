import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

// POST /api/scrape — trigger scraping for a platform
// Body: { platform: "weixin", accountId: "..." }
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { platform, accountId } = body;

    if (platform === "weixin") {
      return await scrapeWeixin(accountId);
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
