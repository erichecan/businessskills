import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const status = searchParams.get("status");

    const hotTopics = await prisma.hotTopic.findMany({
      where: status ? { status } : undefined,
      orderBy: { scrapedAt: "desc" },
      include: { _count: { select: { topics: true } } },
    });

    return NextResponse.json(hotTopics);
  } catch (error) {
    console.error("[GET /api/hot-topics]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { title, platform = "manual", url, engagementScore } = body;

    if (!title?.trim()) {
      return NextResponse.json({ error: true, message: "标题不能为空" }, { status: 400 });
    }

    const hotTopic = await prisma.hotTopic.create({
      data: {
        title: title.trim(),
        platform,
        url: url || null,
        engagementScore: engagementScore ? Number(engagementScore) : null,
      },
    });

    return NextResponse.json(hotTopic, { status: 201 });
  } catch (error) {
    console.error("[POST /api/hot-topics]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}
