import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await req.json();
    const { status } = body;

    const validStatuses = ["pending", "relevant", "irrelevant"];
    if (!validStatuses.includes(status)) {
      return NextResponse.json({ error: true, message: "无效的状态值" }, { status: 400 });
    }

    const hotTopic = await prisma.hotTopic.update({
      where: { id },
      data: { status },
    });

    return NextResponse.json(hotTopic);
  } catch (error) {
    console.error("[PATCH /api/hot-topics/[id]]", error);
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
    await prisma.hotTopic.delete({ where: { id } });
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("[DELETE /api/hot-topics/[id]]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}
