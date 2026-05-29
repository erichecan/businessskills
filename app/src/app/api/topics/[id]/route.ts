import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await req.json();
    const { status, title, angle, format } = body;

    const data: Record<string, string> = {};
    if (status) data.status = status;
    if (title) data.title = title.trim();
    if (angle !== undefined) data.angle = angle?.trim() || "";
    if (format !== undefined) data.format = format || "";

    const topic = await prisma.topic.update({
      where: { id },
      data,
      include: {
        hotTopic: { select: { id: true, title: true, platform: true } },
        _count: { select: { contents: true } },
      },
    });

    return NextResponse.json(topic);
  } catch (error) {
    console.error("[PATCH /api/topics/[id]]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    await prisma.topic.delete({ where: { id } });
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("[DELETE /api/topics/[id]]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}
