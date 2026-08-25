import { createServer, type Server } from 'node:http'

import { expect, test } from '@playwright/test'

import { fetchDownloadBytes } from '../specs/support/download'

let server: Server
let origin = ''
let objectCookie: string | undefined
let objectRequests = 0

test.beforeAll(async () => {
    server = createServer((request, response) => {
        response.setHeader('Access-Control-Allow-Origin', '*')
        if (request.url === '/page') {
            response.writeHead(200, { 'Content-Type': 'text/html' }).end('<main>probe</main>')
            return
        }
        if (request.url === '/redirect') {
            response.writeHead(302, { Location: `${origin}/object` }).end()
            return
        }
        if (request.url === '/object') {
            objectCookie = request.headers.cookie
            objectRequests += 1
        }
        response.writeHead(200, { 'Content-Type': 'application/octet-stream' })
        response.end(Buffer.from('browser-object-boundary'))
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    if (address === null || typeof address === 'string') throw new Error('HTTP probe did not bind.')
    origin = `http://127.0.0.1:${address.port}`
})

test.afterAll(async () => {
    await new Promise<void>((resolve, reject) =>
        server.close((error) => (error === undefined ? resolve() : reject(error))),
    )
})

test('an HTTPS-looking invalid href passes the old assertion but fails the browser fetch', async ({
    page,
}) => {
    const invalidUrl = 'https://example.invalid/expired-signed-object'
    expect(invalidUrl).toMatch(/^https:\/\//)
    await expect(fetchDownloadBytes(page, invalidUrl)).rejects.toThrow(/fetch/i)
})

test('browser fetch returns the real cross-origin bytes and rejects redirects', async ({ page }) => {
    await page.context().addCookies([
        { name: 'session', value: 'must-not-cross', domain: '127.0.0.1', path: '/' },
    ])
    await page.goto(`${origin.replace('127.0.0.1', 'localhost')}/page`)
    const result = await fetchDownloadBytes(page, `${origin}/object`)
    expect(result.status).toBe(200)
    expect(result.byteLength).toBe(Buffer.byteLength('browser-object-boundary'))
    expect(result.sha256).toBe(
        '8bc037c0e329b6ebec1156477c947835535836b15c0101591bbec7c5e3e18b72',
    )
    expect(objectCookie).toBeUndefined()
    const requestsBeforeRedirect = objectRequests
    await expect(fetchDownloadBytes(page, `${origin}/redirect`)).rejects.toThrow(/fetch/i)
    expect(objectRequests).toBe(requestsBeforeRedirect)
})
