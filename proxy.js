import http from 'http';
import https from 'https';
const PORT = 4000;
const RUNPOD_URL = 'https://vh1eq50f5dbuvr-8000.proxy.runpod.net/v1/chat/completions';
const RUNPOD_KEY = 'sk-vh1eq50f5dbuvr';
const ACTUAL_MODEL_NAME = 'meta-llama/Meta-Llama-3-8B-Instruct';
// ─── FIX 1: Flatten Anthropic multi-part content into plain text ──────────────
// RunPod's OpenAI-compatible server only accepts simple string content.
// This converts tool_use, tool_result, and text blocks into readable plain text.
function flattenContent(content) {
    if (!content) return '';
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
        return content.map(part => {
            if (part.type === 'text') {
                return part.text || '';
            } else if (part.type === 'tool_use') {
                const input = part.input ? JSON.stringify(part.input, null, 2) : '';
                return `[Tool Use: ${part.name || 'unknown'}\nInput: ${input}]`;
            } else if (part.type === 'tool_result') {
                const resultContent = Array.isArray(part.content)
                    ? part.content.map(c => c.text || '').join('\n')
                    : (part.content || '');
                return `[Tool Result: ${resultContent}]`;
            } else {
                // Fallback for any unknown part types
                return `[${part.type}: ${JSON.stringify(part)}]`;
            }
        }).filter(Boolean).join('\n');
    }
    return String(content);
}
const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
        try {
            console.log(`[Proxy] Received ${req.method} ${req.url}`);
            // Ignore GET requests and requests with no body (e.g. favicon, health checks)
            if (req.method === 'GET' || !body) {
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ status: 'NanoClaw proxy running' }));
                return;
            }
            let incomingData;
            if (body) {
                incomingData = JSON.parse(body);
                console.log(`[Proxy] Incoming Anthropic model:`, incomingData.model);
            }
            // ─── FIX 2: Cap max_tokens to 2048 (model max is 8192) ───────────────
            // We use 2048 to allow up to 6144 tokens for input history.
            const requestedMaxTokens = incomingData?.max_tokens || 1024;
            // ─── FIX 1 applied: flatten all message content before sending ────────
            const flattenedMessages = (incomingData?.messages || []).map(msg => ({
                role: msg.role,
                content: flattenContent(msg.content),
            }));
            const outgoingData = {
                model: ACTUAL_MODEL_NAME,
                messages: flattenedMessages,
                max_tokens: Math.min(requestedMaxTokens, 2048),
                stream: false
            };
            // Anthropic puts System prompts at the root; OpenAI puts them in the messages array
            if (incomingData?.system) {
                const sysContent = Array.isArray(incomingData.system)
                    ? incomingData.system.map(s => s.text).join('\n')
                    : incomingData.system;
                outgoingData.messages.unshift({ role: 'system', content: sysContent });
            }
            const reqBody = JSON.stringify(outgoingData);
            console.log(`[Proxy] Sending to RunPod as model:`, ACTUAL_MODEL_NAME);
            // 2. Send the converted OpenAI-formatted request
            const proxyReq = https.request(RUNPOD_URL, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${RUNPOD_KEY}`,
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(reqBody)
                }
            }, (proxyRes) => {
                let proxyBody = '';
                proxyRes.on('data', chunk => { proxyBody += chunk; });
                proxyRes.on('end', () => {
                    console.log(`[Proxy] RunPod returned ${proxyRes.statusCode}`);
                    if (proxyRes.statusCode >= 400) {
                        console.error(`[Proxy] Error body:`, proxyBody);
                        res.writeHead(proxyRes.statusCode, proxyRes.headers);
                        res.end(proxyBody);
                        return;
                    }
                    try {
                        const runpodData = JSON.parse(proxyBody);
                        // 3. Convert OpenAI response back to Anthropic format
                        const anthropicResponse = {
                            id: runpodData.id || "msg_123",
                            type: "message",
                            role: "assistant",
                            model: incomingData?.model || "claude-3-haiku-20240307",
                            content: [
                                {
                                    type: "text",
                                    text: runpodData.choices?.[0]?.message?.content || ""
                                }
                            ],
                            stop_reason: "end_turn",
                            stop_sequence: null,
                            usage: {
                                input_tokens: runpodData.usage?.prompt_tokens || 0,
                                output_tokens: runpodData.usage?.completion_tokens || 0
                            }
                        };
                        res.writeHead(200, { 'Content-Type': 'application/json' });
                        res.end(JSON.stringify(anthropicResponse));
                    } catch (e) {
                        console.error('[Proxy] Failed to parse RunPod response', e);
                        res.writeHead(500);
                        res.end('Proxy Parse Error');
                    }
                });
            });
            proxyReq.on('error', (e) => {
                console.error(`[Proxy] Request error:`, e.message);
                res.writeHead(500);
                res.end('Proxy Request Error');
            });
            proxyReq.write(reqBody);
            proxyReq.end();
        } catch (e) {
            console.error(`[Proxy] Top level error:`, e);
            res.writeHead(500);
            res.end('Internal Server Error');
        }
    });
});
server.listen(PORT, () => {
    console.log(`[Proxy] NanoClaw Anthropic->OpenAI translation proxy running on http://localhost:${PORT}`);
});