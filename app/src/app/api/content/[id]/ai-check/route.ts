import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { readSkill } from "@/lib/claude";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const content = await prisma.content.findUnique({ where: { id } });
    if (!content) return NextResponse.json({ error: true, message: "内容不存在" }, { status: 404 });

    const textToCheck = content.finalDraft || content.draft;
    if (!textToCheck) {
      return NextResponse.json({ error: true, message: "请先生成初稿或定稿" }, { status: 400 });
    }

    const systemPrompt = readSkill("eric-ai-check");
    const userMessage = `请对以下内容进行 AI 痕迹检测：\n\n${textToCheck}`;

    return NextResponse.json({ systemPrompt, userMessage, combined: `${systemPrompt}\n\n---\n\n${userMessage}` });
  } catch (error) {
    console.error("[GET /api/content/[id]/ai-check]", error);
    return NextResponse.json({ error: true, message: error instanceof Error ? error.message : "未知错误" }, { status: 500 });
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const { result } = await req.json();
    if (!result?.trim()) return NextResponse.json({ error: true, message: "结果不能为空" }, { status: 400 });

    const updated = await prisma.content.update({
      where: { id },
      data: { aiCheckResult: result.trim() },
    });
    return NextResponse.json(updated);
  } catch (error) {
    console.error("[POST /api/content/[id]/ai-check]", error);
    return NextResponse.json({ error: true, message: error instanceof Error ? error.message : "未知错误" }, { status: 500 });
  }
}
