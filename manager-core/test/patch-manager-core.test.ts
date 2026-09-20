import { afterEach, describe, expect, it } from 'vitest';

import {
  createInternalRequestHeaders,
  OFFICIAL_HTTP_USER_AGENT,
  resolveRequestHeaderProfile,
} from '../upstream/src/modules/proxy-gateway/server/common/utils/request-header-profile';
import { transformClaudeRequestIn } from '../upstream/src/modules/proxy-gateway/antigravity/ClaudeRequestMapper';
import { transformResponse } from '../upstream/src/modules/proxy-gateway/antigravity/ClaudeResponseMapper';
import { StreamingState } from '../upstream/src/modules/proxy-gateway/antigravity/ClaudeStreamingMapper';
import { SignatureStore } from '../upstream/src/modules/proxy-gateway/antigravity/SignatureStore';
import { toOpenAIUsageFromGeminiUsageMetadata } from '../upstream/src/modules/proxy-gateway/antigravity/OpenAIUsageMapper';

describe('manager core patches', () => {
  afterEach(() => {
    SignatureStore.clear();
  });

  it('uses captured official HTTP headers by default without changing a body user agent', () => {
    const profile = resolveRequestHeaderProfile({});
    const headers = createInternalRequestHeaders({
      accessToken: 'token',
      includeProject: true,
      managerUserAgent: 'antigravity/99.0.0 windows/amd64',
      profile,
      project: 'project-1',
    });

    expect(profile).toBe('official');
    expect(headers).toMatchObject({
      'Accept-Encoding': 'gzip',
      'User-Agent': OFFICIAL_HTTP_USER_AGENT,
    });
    expect(headers['x-goog-user-project']).toBeUndefined();
  });

  it('uses the manager header profile only when explicitly configured', () => {
    const profile = resolveRequestHeaderProfile({ AGM_CORE_HEADER_PROFILE: 'manager' });
    const headers = createInternalRequestHeaders({
      accessToken: 'token',
      includeProject: true,
      managerUserAgent: 'antigravity/99.0.0 windows/amd64',
      profile,
      project: 'project-1',
    });

    expect(profile).toBe('manager');
    expect(headers['User-Agent']).toBe('antigravity/99.0.0 windows/amd64');
    expect(headers['x-goog-user-project']).toBe('project-1');
  });

  it('keeps embedded system turns in chronological contents instead of global instructions', () => {
    const body = transformClaudeRequestIn({
      max_tokens: 64,
      messages: [
        { role: 'user', content: 'first' },
        { role: 'system', content: 'turn-specific constraint' },
        { role: 'user', content: 'second' },
      ],
      model: 'gemini-3-flash',
    });

    expect(body.request.contents.map((content) => content.parts[0]?.text)).toEqual([
      'first',
      'turn-specific constraint',
      'second',
    ]);
    expect(body.request.systemInstruction?.parts[0]?.text).not.toContain('turn-specific constraint');
  });

  it('does not attach a newer session signature to an unmatched historical tool call', () => {
    const sessionKey = 'patch-signature-session';
    SignatureStore.store('newer-signature'.repeat(4), sessionKey, 3);

    const body = transformClaudeRequestIn({
      max_tokens: 64,
      messages: [
        {
          role: 'assistant',
          content: [
            { signature: 'previous-signature'.repeat(4), thinking: 'old thought', type: 'thinking' },
          ],
        },
        { role: 'user', content: 'continue' },
        {
          role: 'assistant',
          content: [{ id: 'call-old', input: {}, name: 'tool', type: 'tool_use' }],
        },
      ],
      metadata: { signature_session_key: sessionKey },
      model: 'gemini-3-flash',
      thinking: { budget_tokens: 64, type: 'enabled' },
    });

    const toolCall = body.request.contents[2]?.parts.find((part) => part.functionCall);
    expect(toolCall?.thoughtSignature).toBeUndefined();
  });

  it('reports OpenAI cache details alongside the full prompt and Anthropic input without cache', () => {
    expect(
      toOpenAIUsageFromGeminiUsageMetadata({
        cachedContentTokenCount: 8,
        candidatesTokenCount: 3,
        promptTokenCount: 12,
      }),
    ).toMatchObject({
      completion_tokens: 3,
      prompt_tokens: 12,
      prompt_tokens_details: { cached_tokens: 8 },
      total_tokens: 15,
    });

    expect(
      transformResponse({
        usageMetadata: {
          cachedContentTokenCount: 8,
          candidatesTokenCount: 3,
          promptTokenCount: 12,
        },
      }).usage,
    ).toMatchObject({
      cache_read_input_tokens: 8,
      input_tokens: 4,
      output_tokens: 3,
    });

    const sse = new StreamingState()
      .emitFinish('STOP', {
        cachedContentTokenCount: 8,
        candidatesTokenCount: 3,
        promptTokenCount: 12,
      })
      .find((chunk) => chunk.startsWith('event: message_delta'));
    expect(sse).toBeDefined();
    expect(JSON.parse((sse ?? '').split('data: ')[1]).usage).toMatchObject({
      cache_read_input_tokens: 8,
      input_tokens: 4,
      output_tokens: 3,
    });
  });
});
