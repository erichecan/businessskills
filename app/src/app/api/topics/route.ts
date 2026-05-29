import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const status = searchParams.get("status");

    const topics = await prisma.topic.findMany({
      where: status ? { status } : undefined,
      orderBy: { createdAt: "desc" },
      include: {
        hotTopic: { select: { id: true, title: true, platform: true } },
        _count: { select: { contents: true } },
      },
    });

    return NextResponse.json(topics);
  } catch (error) {
    console.error("[GET /api/topics]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { title, angle, format, hotTopicId } = body;

    if (!title?.trim()) {
      return NextResponse.json({ error: true, message: "选题标题不能为空" }, { status: 400 });
    }

    const topic = await prisma.topic.create({
      data: {
        title: title.trim(),
        angle: angle?.trim() || null,
        format: format || null,
        hotTopicId: hotTopicId || null,
      },
      include: {
        hotTopic: { select: { id: true, title: true, platform: true } },
      },
    });

    return NextResponse.json(topic, { status: 201 });
  } catch (error) {
    console.error("[POST /api/topics]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}
