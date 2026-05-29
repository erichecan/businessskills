import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const status = searchParams.get("status");

    const contents = await prisma.content.findMany({
      where: status ? { status } : undefined,
      orderBy: { updatedAt: "desc" },
      include: {
        topic: {
          select: { id: true, title: true, format: true },
        },
      },
    });

    return NextResponse.json(contents);
  } catch (error) {
    console.error("[GET /api/content]", error);
    return NextResponse.json(
      { error: true, message: error instanceof Error ? error.message : "未知错误" },
      { status: 500 }
    );
  }
}
