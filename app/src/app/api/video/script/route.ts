import { NextRequest } from 'next/server'
import { spawn } from 'child_process'
import { mkdir, writeFile, readFile } from 'fs/promises'
import path from 'path'
import { randomUUID } from 'crypto'
import { existsSync } from 'fs'
import { prisma } from '@/lib/db'

const PROJECT_ROOT = path.resolve(process.cwd(), '..')
const SCRIPTS_DIR = path.join(PROJECT_ROOT, 'scripts')
const EPISODES_DIR = path.join(process.cwd(), 'public', 'video')

function runPython(args: string[]): Promise<{ stdout: string; code: number }> {
  return new Promise((resolve) => {
    const proc = spawn('python3', args, { env: process.env })
    let stdout = ''
    proc.stdout.on('data', (d) => { stdout += d.toString() })
    proc.stderr.on('data', (d) => { stdout += d.toString() })
    proc.on('close', (code) => resolve({ stdout, code: code ?? 0 }))
  })
}

export async function POST(req: NextRequest) {
  try {
    const { topic, contentId } = await req.json()
    if (!topic?.trim()) return Response.json({ error: '话题不能为空' }, { status: 400 })

    const episodeId = randomUUID()
    const episodeDir = path.join(EPISODES_DIR, episodeId)
    await mkdir(episodeDir, { recursive: true })
    await writeFile(path.join(episodeDir, 'topic.txt'), topic.trim())

    if (contentId) {
      await prisma.content.update({
        where: { id: contentId },
        data: { videoEpisodeId: episodeId },
      }).catch((e) => console.error('[video/script] 关联 Content 失败', e))
    }

    const { stdout, code } = await runPython([
      path.join(SCRIPTS_DIR, 'make_script.py'),
      '--topic', topic.trim(),
      '--output-dir', episodeDir,
    ])

    const scriptPath = path.join(episodeDir, 'script.txt')
    const boardPath = path.join(episodeDir, 'storyboard.txt')

    if (!existsSync(scriptPath) || code !== 0) {
      return Response.json({ error: '生成失败', detail: stdout }, { status: 500 })
    }

    const script = await readFile(scriptPath, 'utf-8')
    const boardRaw = await readFile(boardPath, 'utf-8')
    const storyboard = boardRaw.split('\n').filter(l => l.trim())

    return Response.json({ episodeId, script: script.trim(), storyboard })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 })
  }
}
