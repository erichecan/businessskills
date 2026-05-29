import Anthropic from "@anthropic-ai/sdk";
import fs from "fs";
import path from "path";

const client = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
});

// Skills are located at ../../skills/ relative to the app/ directory
const SKILLS_BASE = path.join(process.cwd(), "..", "skills");

export function readSkill(skillName: string): string {
  const skillPath = path.join(SKILLS_BASE, skillName, "SKILL.md");
  if (!fs.existsSync(skillPath)) {
    throw new Error(`Skill not found: ${skillName}`);
  }
  return fs.readFileSync(skillPath, "utf-8");
}

export async function callClaude(
  systemPrompt: string,
  userMessage: string,
  model = "claude-opus-4-5"
): Promise<string> {
  const message = await client.messages.create({
    model,
    max_tokens: 4096,
    system: systemPrompt,
    messages: [{ role: "user", content: userMessage }],
  });

  const content = message.content[0];
  if (content.type !== "text") throw new Error("Unexpected response type from Claude");
  return content.text;
}
