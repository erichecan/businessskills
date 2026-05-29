import { NextRequest } from "next/server";
import { prisma } from "@/lib/db";
import { readSkill } from "@/lib/claude";
import fs from "fs";
import path from "path";

type Atom = {
  id: string;
  knowledge: string;
  original?: string;
  source?: string;
  date?: string;
  topics?: string[];
  skills?: string[];
  type?: string;
  confidence?: string;
};

function loadAtoms(): Atom[] {
  const atomsBase = path.join(process.cwd(), "..", "知识库", "Eric原子库");
  const files = ["atoms.jsonl", "atoms_2026Q2.jsonl"];
  const atoms: Atom[] = [];
  const seen = new Set<string>();

  for (const file of files) {
    const filePath = path.join(atomsBase, file);
    if (!fs.existsSync(filePath)) continue;
    const lines = fs.readFileSync(filePath, "utf-8").split("\n").filter(Boolean);
    for (const line of lines) {
      try {
        const atom: Atom = JSON.parse(line);
        if (atom.id && !seen.has(atom.id + atom.knowledge)) {
          seen.add(atom.id + atom.knowledge);
          atoms.push(atom);
        }
      } catch {
        // skip malformed lines
      }
    }
  }
  return atoms;
}

function matchAtoms(atoms: Atom[], hotTopicTitle: string): Atom[] {
  const keywords = hotTopicTitle
    .toLowerCase()
    .split(/[\s，。！？、,.!?]+/)
    .filter((k) => k.length >= 2);

  if (keywords.length === 0) return atoms.slice(0, 10);

  const scored = atoms.map((atom) => {
    const text = [
      atom.knowledge,
      atom.original,
      ...(atom.topics ?? []),
    ]
      .join(" ")
      .toLowerCase();

    const score = keywords.reduce(
      (acc, kw) => acc + (text.includes(kw) ? 1 : 0),
      0
    );
    return { atom, score };
  });

  const matched = scored
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((s) => s.atom);

  // Return top matched + some unmatched for context, deduped
  const unmatched = scored.filter((s) => s.score === 0).map((s) => s.atom);
  return [...matched.slice(0, 8), ...unmatched.slice(0, 4)];
}

export async function GET(req: NextRequest) {
  const hotTopicId = req.nextUrl.searchParams.get("hotTopicId");
  if (!hotTopicId) {
    return Response.json({ message: "缺少 hotTopicId 参数" }, { status: 400 });
  }

  const hotTopic = await prisma.hotTopic.findUnique({
    where: { id: hotTopicId },
  });
  if (!hotTopic) {
    return Response.json({ message: "热点不存在" }, { status: 404 });
  }

  // Load and match atoms
  const allAtoms = loadAtoms();
  const relevantAtoms = matchAtoms(allAtoms, hotTopic.title);

  // Build atom context
  const atomContext =
    relevantAtoms.length > 0
      ? relevantAtoms
          .map(
            (a, i) =>
              `原子 ${i + 1}：${a.knowledge}${a.original ? `\n（原始：${a.original}）` : ""}`
          )
          .join("\n\n")
      : "（暂无相关原子，请基于热点本身推断）";

  // Read eric-content skill
  let skillContent = "";
  try {
    skillContent = readSkill("eric-content");
  } catch {
    skillContent = "（技能文件未找到）";
  }

  const systemPrompt = skillContent;

  const userMessage = `# 热点话题
「${hotTopic.title}」

# 我的知识原子库（与该热点相关的洞察）

${atomContext}

---

请根据以上热点和知识原子，生成选题建议。

**要求：**
1. 给出 10 个「相关选题」：内容与该热点直接相关，且与我的原子库主题吻合，有机会成为爆款
2. 给出 10 个「不相关选题」：跟这个热点关联不大，但话题本身有传播潜力的选题

**每个选题包含：**
- title：选题标题（20字以内）
- angle：切入角度（核心视角或差异化点，15字以内）
- format：建议格式（图文 / 短视频 / 长视频 三选一）
- viability：可行性评分 1-5（5=强烈推荐）
- note：简短说明（30字以内，说明为什么选或为什么不选）

**请严格按以下 JSON 格式输出（包在代码块内）：**

\`\`\`json
{
  "related": [
    {"title": "...", "angle": "...", "format": "图文", "viability": 4, "note": "..."},
    ...
  ],
  "unrelated": [
    {"title": "...", "angle": "...", "format": "图文", "viability": 3, "note": "..."},
    ...
  ]
}
\`\`\``;

  const combined = `[系统提示]\n${systemPrompt}\n\n[用户消息]\n${userMessage}`;

  return Response.json({ systemPrompt, userMessage, combined });
}
