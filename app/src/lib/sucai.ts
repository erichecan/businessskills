import fs from "fs";
import path from "path";

/**
 * 素材库桥接助手
 * 把小红书定时采集任务产出的文件（记忆库.csv / 选题库.csv / 当日 xlsx 旁的 csv）
 * 读取、解析、归一化，供 app 内容工坊入库使用。
 *
 * 素材库默认位于 app 同级的 ../xhs/素材库，可用环境变量 SUCAI_DIR 覆盖。
 */
export function sucaiDir(): string {
  return (
    process.env.SUCAI_DIR ??
    path.join(process.cwd(), "..", "xhs", "素材库")
  );
}

export function sucaiFile(name: string): string {
  return path.join(sucaiDir(), name);
}

/** 把"37万""8.7万""2,280""—"等热度字符串归一化为数值（无法识别返回 null） */
export function parseHeat(raw: string | undefined | null): number | null {
  if (raw == null) return null;
  const s = String(raw).trim();
  if (s === "" || s === "—" || s === "-") return null;
  let m = s.match(/^([\d.]+)\s*万/);
  if (m) return Math.round(parseFloat(m[1]) * 10000);
  m = s.match(/^([\d.]+)\s*w/i);
  if (m) return Math.round(parseFloat(m[1]) * 10000);
  m = s.match(/^([\d,]+)$/);
  if (m) return parseInt(m[1].replace(/,/g, ""), 10);
  return null;
}

/**
 * 轻量 CSV 解析（零依赖）。支持双引号包裹字段、字段内逗号与转义双引号、\r\n。
 * 返回以表头为 key 的对象数组。
 */
export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let field = "";
  let row: string[] = [];
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (c === "\r") {
      // ignore, handled by \n
    } else {
      field += c;
    }
  }
  // flush last field/row
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  if (rows.length === 0) return [];
  const header = rows[0].map((h) => h.trim());
  return rows
    .slice(1)
    .filter((r) => r.some((cell) => cell.trim() !== ""))
    .map((r) => {
      const obj: Record<string, string> = {};
      header.forEach((h, idx) => {
        obj[h] = (r[idx] ?? "").trim();
      });
      return obj;
    });
}

export function readCsvFile(name: string): Record<string, string>[] {
  const fp = sucaiFile(name);
  if (!fs.existsSync(fp)) return [];
  return parseCsv(fs.readFileSync(fp, "utf-8"));
}

/** 归一化标题：去 emoji / 空白 / 标点，作为去重兜底键（noteId 缺失时用） */
export function normalizeTitle(title: string): string {
  return (title || "")
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{2049}\u{203C}]/gu, "")
    .replace(/[\s　]+/g, "")
    .replace(/[，。！？、,.!?：:；;~～·…—\-（）()【】\[\]"'""'']/g, "")
    .toLowerCase()
    .trim();
}

export type SucaiNote = {
  title: string;
  url: string | null;
  keyword: string | null;
  source: string | null;
  firstSeen: string | null;
  heatRaw: string | null;
  heatNum: number | null;
};

/** 读取 职场面试_记忆库.csv → 结构化笔记数组 */
export function readMemoryNotes(): SucaiNote[] {
  const rows = readCsvFile("职场面试_记忆库.csv");
  return rows.map((r) => ({
    title: r["标题"] ?? "",
    url: r["URL"] || null,
    keyword: r["关键词"] || null,
    source: r["来源"] || null,
    firstSeen: r["首次收录日期"] || null,
    heatRaw: r["热度"] || null,
    heatNum: parseHeat(r["热度"]),
  }));
}

export type SucaiTopic = {
  date: string | null;
  name: string;
  board: string | null;
  status: string | null;
};

/** 读取 选题库.csv → 结构化选题数组 */
export function readTopicLibrary(): SucaiTopic[] {
  const rows = readCsvFile("选题库.csv");
  return rows.map((r) => ({
    date: r["日期"] || null,
    name: r["选题名"] ?? "",
    board: r["归属板块"] || null,
    status: r["状态"] || null,
  }));
}
