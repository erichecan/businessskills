import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { readSkill } from "@/lib/claude";

// GET — return the prompt to copy into Claude manually
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
    const topicContext = [
      `选题：${content.topic.title}`,
      content.topic.angle ? `角度：${content.topic.angle}` : null,
      content.topic.format ? `格式：${content.topic.format}` : null,
      content.topic.hotTopic ? `来源热点：${content.topic.hotTopic.title}` : null,
    ].filter(Boolean).join("\n");

    const draftText = content.finalDraft || content.draft;
    const userMessage = draftText
      ? `请对以下内容做五维诊断（诊断模式，只诊断不代写）：\n\n${topicContext}\n\n--- 稿件 ---\n\n${draftText}`
      : `请对以下选题做内容诊断分析（尚无稿件，先诊断选题与内容方向）：\n\n${topicContext}`;

    return NextResponse.json({ systemPrompt, userMessage, combined: `${systemPrompt}\n\n---\n\n${userMessage}` });
  } catch (error) {
    console.error("[GET /api/content/[id]/diagnose]", error);
    return NextResponse.json({ error: true, message: error instanceof Error ? error.message : "未知错误" }, { status: 500 });
  }
}

// POST — save result provided by user (no Claude API call)
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
      data: { diagnosisResult: result.trim(), status: "drafting" },
    });
    return NextResponse.json(updated);
  } catch (error) {
    console.error("[POST /api/content/[id]/diagnose]", error);
    return NextResponse.json({ error: true, message: error instanceof Error ? error.message : "未知错误" }, { status: 500 });
  }
}
