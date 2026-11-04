// カタログ一覧の API クライアントを、fetch をフェイクにして検証する（Seam 2）
import { describe, expect, it } from 'vitest'
import { createApiClient } from '../api/client'
import { fakeFetch, jsonResponse, TEST_CONFIG } from './helpers'

const agentPage = {
  items: [
    {
      recordId: 'rec-1',
      name: 'order-agent',
      displayName: '受注エージェント',
      description: '受注を処理する',
      recordType: 'AGENT',
      descriptorTypes: ['a2aAgentCard'],
      isA2A: true,
      recordVersion: '1.2.0',
      updatedAt: '2026-11-02T00:00:00Z',
    },
  ],
  nextToken: 'page-2/token+=',
}

function clientWith(responses: (Response | Error)[]) {
  const { fetchImpl, calls } = fakeFetch(responses)
  const client = createApiClient({
    baseUrl: TEST_CONFIG.apiBaseUrl,
    fetch: fetchImpl,
    getAccessToken: () => 'access-token-value',
  })
  return { client, calls }
}

describe('カタログ一覧の取得', () => {
  it('種別を指定して1ページ目を取得する', async () => {
    const { client, calls } = clientWith([jsonResponse(200, agentPage)])

    await expect(client.listCatalog('AGENT')).resolves.toEqual(agentPage)

    expect(calls[0].url).toBe('https://api.example.com/api/catalog?type=AGENT')
    expect(new Headers(calls[0].init?.headers).get('authorization')).toBe(
      'Bearer access-token-value',
    )
  })

  it('前のページで受け取った nextToken を URL エンコードして渡す', async () => {
    const { client, calls } = clientWith([jsonResponse(200, { items: [], nextToken: null })])

    await client.listCatalog('MCP', 'page-2/token+=')

    expect(calls[0].url).toBe(
      'https://api.example.com/api/catalog?type=MCP&nextToken=page-2%2Ftoken%2B%3D',
    )
  })

  it('取得に失敗したら、共通エラー DTO の日本語メッセージを返す', async () => {
    const { client } = clientWith([
      jsonResponse(502, {
        error: {
          code: 'DISCOVERY_UNAVAILABLE',
          message: 'カタログを取得できませんでした。時間をおいて再度お試しください。',
          requestId: 'req-1',
        },
      }),
    ])

    await expect(client.listCatalog('SKILL')).rejects.toMatchObject({
      status: 502,
      code: 'DISCOVERY_UNAVAILABLE',
      requiresLogin: false,
    })
  })
})
