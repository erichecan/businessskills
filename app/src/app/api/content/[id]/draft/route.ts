import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { readSkill } from "@/lib/claude";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const content = await prisma.content.findUnique({
      where: { id },
      include: { topic: { include: { hotTopic: true } } },
    });
    if (!content) return NextResponse.json({ error: true, message: "内容不存在" }, { status: 404 });

    const systemPrompt = readSkill("eric-content");
    const parts = [
      `选题：${content.topic.title}`,
      content.topic.angle ? `角度：${content.topic.angle}` : null,
      content.topic.format ? `格式：${content.topic.format}` : null,
      content.topic.hotTopic ? `来源热点：${content.topic.hotTopic.title}` : null,
      content.diagnosisResult ? `\n诊断分析结果：\n${content.diagnosisResult}` : null,
    ].filter(Boolean).join("\n");

    const userMessage = `请根据以下信息生成小红书内容初稿：\n\n${parts}`;

    return NextResponse.json({ systemPrompt, userMessage, combined: `${systemPrompt}\n\n---\n\n${userMessage}` });
  } catch (error) {
    console.error("[GET /api/content/[id]/draft]", error);
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
      data: { draft: result.trim(), status: "reviewing" },
    });
    return NextResponse.json(updated);
  } catch (error) {
    console.error("[POST /api/content/[id]/draft]", error);
    return NextResponse.json({ error: true, message: error instanceof Error ? error.message : "未知错误" }, { status: 500 });
  }
}
