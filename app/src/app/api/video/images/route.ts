import { NextRequest } from 'next/server'
import { spawn } from 'child_process'
import { writeFile } from 'fs/promises'
import path from 'path'

const PROJECT_ROOT = path.resolve(process.cwd(), '..')
const SCRIPTS_DIR = path.join(PROJECT_ROOT, 'scripts')
const EPISODES_DIR = path.join(process.cwd(), 'public', 'video')
const REF_IMAGE = path.join(PROJECT_ROOT, 'test_images', 'test_chibi_03.png')

export async function POST(req: NextRequest) {
  const { episodeId, storyboard } = await req.json()
  const episodeDir = path.join(EPISODES_DIR, episodeId)

  await writeFile(path.join(episodeDir, 'storyboard.txt'), storyboard.join('\n'))

  const encoder = new TextEncoder()

  const stream = new ReadableStream({
    async start(controller) {
      const send = (data: object) => {
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
        } catch {}
      }

      let buffer = ''
      let currentScene = 0

      const proc = spawn('python3', [
        path.join(SCRIPTS_DIR, 'make_images.py'),
        '--scenes', path.join(episodeDir, 'storyboard.txt'),
        '--output-dir', episodeDir,
        '--ref', REF_IMAGE,
      ], { env: process.env })

      proc.stdout.on('data', (chunk) => {
        buffer += chunk.toString()
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          const sceneMatch = line.match(/\[(\d+)\/\d+\]/)
          if (sceneMatch) currentScene = parseInt(sceneMatch[1])

          if (line.includes('✅') && line.includes('.png')) {
            send({ type: 'image', index: currentScene, url: `/video/${episodeId}/scene${currentScene}.png` })
          } else if (line.includes('❌') && line.includes('生成失败') && currentScene > 0) {
            send({ type: 'error', index: currentScene })
          }
          if (line.trim()) send({ type: 'log', message: line.trim() })
        }
      })

      proc.stderr.on('data', (chunk) => {
        send({ type: 'log', message: chunk.toString().trim() })
      })

      proc.on('close', () => {
        send({ type: 'done' })
        controller.close()
      })
    }
  })

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  })
}
