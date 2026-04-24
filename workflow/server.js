import express from "express";
import Anthropic from "@anthropic-ai/sdk";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import dotenv from "dotenv";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, ".env") });

const app = express();
app.use(express.json({ limit: "1mb" }));

const SKILLS_DIR = path.join(__dirname, "..", "skills");
const ATOMS_DIR = path.join(__dirname, "..", "知识库", "Eric原子库");
const UPSTREAM_ATOMS_DIR = path.join(__dirname, "..", "知识库", "原子库");

// --- Claude API ---
const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const MODEL = process.env.CLAUDE_MODEL || "claude-sonnet-4-20250514";

// --- Helpers ---
function readSkill(name) {
  const fp = path.join(SKILLS_DIR, name, "SKILL.md");
  if (!fs.existsSync(fp)) throw new Error(`Skill not found: ${name}`);
  return fs.readFileSync(fp, "utf-8");
}

function readAtomsFromFile(fp, source) {
  if (!fs.existsSync(fp)) return [];
  const lines = fs.readFileSync(fp, "utf-8").split("\n").filter(Boolean);
  const atoms = [];
  for (const line of lines) {
    try {
      const atom = JSON.parse(line);
      atom._source = source; // 标记来源
      atoms.push(atom);
    } catch (e) {
      console.warn(`Skipping invalid atom line (${source}):`, line.slice(0, 50));
    }
  }
  return atoms;
}

function readAtoms() {
  const upstream = readAtomsFromFile(
    path.join(UPSTREAM_ATOMS_DIR, "atoms.jsonl"),
    "upstream"
  );
  const eric = readAtomsFromFile(
    path.join(ATOMS_DIR, "atoms.jsonl"),
    "eric"
  );
  return [...upstream, ...eric];
}

function getQuarter() {
  const d = new Date();
  const q = Math.ceil((d.getMonth() + 1) / 3);
  return `${d.getFullYear()}Q${q}`;
}

function getNextAtomId() {
  const quarter = getQuarter();
  const fp = path.join(ATOMS_DIR, `atoms_${quarter}.jsonl`);
  if (!fs.existsSync(fp)) return `${quarter}_001`;
  const lines = fs.readFileSync(fp, "utf-8").split("\n").filter(Boolean);
  if (lines.length === 0) return `${quarter}_001`;
  const last = JSON.parse(lines[lines.length - 1]);
  const seq = parseInt(last.id.split("_")[1], 10) + 1;
  return `${quarter}_${String(seq).padStart(3, "0")}`;
}

// --- Routes ---

// Serve the HTML page
app.get("/", (_req, res) => {
  res.sendFile(path.join(__dirname, "index.html"));
});

// List available skills
app.get("/api/skills", (_req, res) => {
  const dirs = fs.readdirSync(SKILLS_DIR).filter((d) => {
    return fs.existsSync(path.join(SKILLS_DIR, d, "SKILL.md"));
  });
  res.json(dirs);
});

// Get atoms
app.get("/api/atoms", (_req, res) => {
  res.json(readAtoms());
});

// Get next atom ID
app.get("/api/atoms/next-id", (_req, res) => {
  res.json({ id: getNextAtomId() });
});

// Append atom
app.post("/api/atoms", (req, res) => {
  const atom = req.body;
  const line = JSON.stringify(atom, null, 0) + "\n";
  const quarter = getQuarter();

  // Append to quarterly file
  const qfp = path.join(ATOMS_DIR, `atoms_${quarter}.jsonl`);
  fs.appendFileSync(qfp, line);

  // Append to full file
  const afp = path.join(ATOMS_DIR, "atoms.jsonl");
  fs.appendFileSync(afp, line);

  // Update README count
  const readmePath = path.join(ATOMS_DIR, "README.md");
  if (fs.existsSync(readmePath)) {
    let readme = fs.readFileSync(readmePath, "utf-8");
    const allAtoms = readAtoms();
    readme = readme.replace(
      /知识原子数量：\d+/,
      `知识原子数量：${allAtoms.length}`
    );
    fs.writeFileSync(readmePath, readme);
  }

  res.json({ ok: true, id: atom.id, total: readAtoms().length });
});

// Call a skill via Claude API
app.post("/api/call-skill", async (req, res) => {
  const { skill, userMessage, context } = req.body;

  if (!skill || !userMessage) {
    return res.status(400).json({ error: "Missing skill or userMessage" });
  }

  try {
    let systemPrompt = readSkill(skill);

    // Inject product definition context
    const productContext = `
【Eric 的产品定义】
帮 40 岁的人用 6 个月找到一个值得投入 10 年的方向。
产品 = 诊断框架（帮人搞清楚卡在哪里）+ 陪跑过程（帮人在执行中不放弃）。
内容是产品的广告，不是独立存在的。
`;

    // If there's context from previous phases, include it
    let fullUserMessage = userMessage;
    if (context) {
      fullUserMessage = `【前序阶段的输出，供参考】\n${context}\n\n---\n\n【本次输入】\n${userMessage}`;
    }

    systemPrompt = productContext + "\n\n" + systemPrompt;

    const message = await client.messages.create({
      model: MODEL,
      max_tokens: 4096,
      system: systemPrompt,
      messages: [{ role: "user", content: fullUserMessage }],
    });

    const text =
      message.content[0].type === "text" ? message.content[0].text : "";
    res.json({ result: text });
  } catch (err) {
    console.error("[call-skill error]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// Free-form Claude call (for atom extraction etc.)
app.post("/api/call-claude", async (req, res) => {
  const { system, userMessage } = req.body;
  try {
    const message = await client.messages.create({
      model: MODEL,
      max_tokens: 4096,
      system: system || "You are a helpful assistant. Respond in Chinese.",
      messages: [{ role: "user", content: userMessage }],
    });
    const text =
      message.content[0].type === "text" ? message.content[0].text : "";
    res.json({ result: text });
  } catch (err) {
    console.error("[call-claude error]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// --- Start ---
const PORT = process.env.PORT || 3210;
app.listen(PORT, () => {
  console.log(`\n🚀 Eric 工作流已启动: http://localhost:${PORT}\n`);
  console.log(`   Skills 目录: ${SKILLS_DIR}`);
  console.log(`   原子库目录: ${ATOMS_DIR}`);
  console.log(`   模型: ${MODEL}\n`);
});
