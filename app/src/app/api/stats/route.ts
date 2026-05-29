import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET() {
  const [
    hotTopicsTotal, hotTopicsRelevant,
    topicsTotal, topicsApproved,
    contentsTotal, contentsReady, contentsDrafting,
  ] = await Promise.all([
    prisma.hotTopic.count(),
    prisma.hotTopic.count({ where: { status: "relevant" } }),
    prisma.topic.count(),
    prisma.topic.count({ where: { status: "approved" } }),
    prisma.content.count(),
    prisma.content.count({ where: { status: "ready" } }),
    prisma.content.count({ where: { status: "drafting" } }),
  ]);

  return NextResponse.json({
    hotTopicsTotal, hotTopicsRelevant,
    topicsTotal, topicsApproved,
    contentsTotal, contentsReady, contentsDrafting,
  });
}
