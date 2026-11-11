import { createReadStream, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { extname, relative, resolve } from 'node:path'

const root = resolve(process.env.AMRE_DASHBOARD_WEB_ROOT ?? 'dist')
const port = Number(process.env.AMRE_DASHBOARD_PORT ?? 4178)
const types = {
  '.css': 'text/css', '.html': 'text/html', '.js': 'text/javascript',
  '.json': 'application/json', '.woff': 'font/woff', '.woff2': 'font/woff2',
}
const server = createServer((request, response) => {
  try {
    const url = new URL(request.url, 'http://127.0.0.1')
    const pathname = decodeURIComponent(url.pathname)
    const file = resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname))
    if (relative(root, file).startsWith('..') || !statSync(file).isFile()) throw new Error()
    response.setHeader('Content-Type', types[extname(file)] ?? 'application/octet-stream')
    createReadStream(file).pipe(response)
  } catch {
    response.writeHead(404).end()
  }
})
server.listen(port, '127.0.0.1', () => console.log('Local: http://127.0.0.1:' + port))
