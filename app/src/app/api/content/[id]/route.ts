import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;

    const content = await prisma.content.findUnique({
      where: { id },
      include: {
        topic: {
          include: {
            hotTopic: { select: { id: true, title: true, platform: true } },
          },
        },
      },
    });

    if (!content) {
      return NextResponse.json({ error: true, message: "内容不存在" }, { status: 404 });
    }

    return NextResponse.json(content);
  } catch (error) {
    console.error("[GET /api/content/[id]]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await req.json();

    const allowedFields = ["draft", "finalDraft", "status", "coverImageUrl"];
    const data: Record<string, string> = {};
    for (const field of allowedFields) {
      if (body[field] !== undefined) data[field] = body[field];
    }

    const content = await prisma.content.update({ where: { id }, data });
    return NextResponse.json(content);
  } catch (error) {
    console.error("[PATCH /api/content/[id]]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}
