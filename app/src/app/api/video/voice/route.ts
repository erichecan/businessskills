import { NextRequest } from 'next/server'
import { spawn } from 'child_process'
import { writeFile } from 'fs/promises'
import path from 'path'
import { existsSync } from 'fs'

const PROJECT_ROOT = path.resolve(process.cwd(), '..')
const SCRIPTS_DIR = path.join(PROJECT_ROOT, 'scripts')
const EPISODES_DIR = path.join(process.cwd(), 'public', 'video')

export async function POST(req: NextRequest) {
  try {
    const { episodeId, script } = await req.json()
    const episodeDir = path.join(EPISODES_DIR, episodeId)
    const scriptPath = path.join(episodeDir, 'script.txt')
    const voicePath = path.join(episodeDir, 'voice.mp3')

    await writeFile(scriptPath, script)

    if (!process.env.OPENAI_API_KEY) {
      return Response.json({ error: '未配置 OPENAI_API_KEY，请在 .env.local 中设置后重启服务' }, { status: 400 })
    }

    await new Promise<void>((resolve, reject) => {
      const proc = spawn('python3', [
        path.join(SCRIPTS_DIR, 'make_voice.py'),
        '--file', scriptPath,
        '--output', voicePath,
      ], { env: process.env })
      proc.on('close', (code) => code === 0 ? resolve() : reject(new Error('配音生成失败')))
    })

    if (!existsSync(voicePath)) {
      return Response.json({ error: '配音文件未生成' }, { status: 500 })
    }

    return Response.json({ audioUrl: `/video/${episodeId}/voice.mp3` })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 })
  }
}
