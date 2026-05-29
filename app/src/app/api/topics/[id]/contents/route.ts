import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

// POST /api/topics/[id]/contents — create a content draft for a topic
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;

    const topic = await prisma.topic.findUnique({ where: { id } });
    if (!topic) {
      return NextResponse.json({ error: true, message: "选题不存在" }, { status: 404 });
    }

    const content = await prisma.content.create({
      data: { topicId: id },
    });

    return NextResponse.json(content, { status: 201 });
  } catch (error) {
    console.error("[POST /api/topics/[id]/contents]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}
