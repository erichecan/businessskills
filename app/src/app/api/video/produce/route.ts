import { NextRequest } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import { existsSync } from 'fs'
import { readdir } from 'fs/promises'

const PROJECT_ROOT = path.resolve(process.cwd(), '..')
const SCRIPTS_DIR = path.join(PROJECT_ROOT, 'scripts')
const EPISODES_DIR = path.join(process.cwd(), 'public', 'video')

export async function POST(req: NextRequest) {
  try {
    const { episodeId } = await req.json()
    const episodeDir = path.join(EPISODES_DIR, episodeId)
    const voicePath = path.join(episodeDir, 'voice.mp3')
    const bgmPath = path.join(episodeDir, 'bgm.mp3')
    const outputPath = path.join(episodeDir, 'output.mp4')

    const files = await readdir(episodeDir)
    const images = files
      .filter(f => /^scene\d+\.png$/.test(f))
      .sort((a, b) => {
        const na = parseInt(a.match(/\d+/)![0])
        const nb = parseInt(b.match(/\d+/)![0])
        return na - nb
      })
      .map(f => path.join(episodeDir, f))

    if (images.length === 0) return Response.json({ error: '没有找到场景图片' }, { status: 400 })
    if (!existsSync(voicePath)) return Response.json({ error: '缺少配音文件 voice.mp3' }, { status: 400 })
    if (!existsSync(bgmPath)) return Response.json({ error: '缺少 BGM 文件，请将 bgm.mp3 放到 episode 目录' }, { status: 400 })

    await new Promise<void>((resolve, reject) => {
      const proc = spawn('python3', [
        path.join(SCRIPTS_DIR, 'produce_video.py'),
        '--images', ...images,
        '--voice', voicePath,
        '--bgm', bgmPath,
        '--output', outputPath,
      ], { env: process.env })
      proc.on('close', (code) => code === 0 ? resolve() : reject(new Error('视频合成失败')))
    })

    if (!existsSync(outputPath)) {
      return Response.json({ error: '视频文件未生成，请检查 ffmpeg 是否已安装' }, { status: 500 })
    }

    return Response.json({ videoUrl: `/video/${episodeId}/output.mp4` })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 })
  }
}
