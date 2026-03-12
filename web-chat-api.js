
import http from 'http';

const API_PORT = 3001;
const PROXY_PORT = 4000; // your existing proxy.js

// In-memory session store — keeps conversation history per user
const sessions = new Map();

function getOrCreateSession(sessionId) {
    if (!sessions.has(sessionId)) {
        sessions.set(sessionId, []);
    }
    return sessions.get(sessionId);
}

function buildMessages(sessionId, userMessage) {
    const history = getOrCreateSession(sessionId);

    history.push({
        role: 'user',
        content: userMessage
    });

    // Keep last 20 messages
    return history.slice(-20);
}

function saveAssistantReply(sessionId, reply) {
    const history = getOrCreateSession(sessionId);
    history.push({
        role: 'assistant',
        content: reply
    });
}

function callProxy(messages, systemPrompt) {
    return new Promise((resolve, reject) => {

        const body = JSON.stringify({
            model: 'claude-3-haiku-20240307', // proxy ignores this
            max_tokens: 1024,
            system: systemPrompt,
            messages
        });

        const req = http.request(
            {
                hostname: 'localhost',
                port: PROXY_PORT,
                path: '/',
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(body)
                }
            },
            (res) => {
                let data = '';

                res.on('data', chunk => {
                    data += chunk;
                });

                res.on('end', () => {
                    try {
                        const parsed = JSON.parse(data);
                        const text = parsed?.content?.[0]?.text || '';

                        if (!text) {
                            reject(new Error('Empty response from proxy: ' + data));
                        } else {
                            resolve(text);
                        }

                    } catch (e) {
                        reject(new Error('Failed to parse proxy response: ' + data));
                    }
                });
            }
        );

        req.on('error', reject);
        req.write(body);
        req.end();
    });
}


// ---------------------------------------------------------------
// SYSTEM PROMPT
// ---------------------------------------------------------------
const SYSTEM_PROMPT = `
You are the AI assistant for Hashtee.

Hashtee builds AI systems that help businesses automate processes and
make faster decisions using data.

Website: https://hashteelab.com

Response Style Rules:
- Speak naturally like a helpful human.
- Use simple conversational language.
- Maximum 1–2 sentences only.
- Do NOT use bullet points.
- Avoid long explanations.
- Focus on the main benefit for the user.

If the question requires internal company details,
politely suggest contacting the Hashtee team at https://hashteelab.com.
`;


// ---------------------------------------------------------------
// HTTP SERVER
// ---------------------------------------------------------------
const server = http.createServer(async (req, res) => {

    // CORS
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
    }

    // Health check
    if (req.method === 'GET' && req.url === '/health') {

        res.writeHead(200, { 'Content-Type': 'application/json' });

        res.end(JSON.stringify({
            status: 'ok',
            service: 'nanoclaw-web-chat'
        }));

        return;
    }

    // Chat endpoint
    if (req.method === 'POST' && req.url === '/query') {

        let body = '';

        req.on('data', chunk => {
            body += chunk;
        });

        req.on('end', async () => {

            try {

                const {
                    prompt,
                    sessionId = 'default',
                    systemPrompt
                } = JSON.parse(body);

                if (!prompt) {

                    res.writeHead(400, { 'Content-Type': 'application/json' });

                    res.end(JSON.stringify({
                        error: 'prompt is required'
                    }));

                    return;
                }

                console.log(`[web-chat-api] session=${sessionId} prompt="${prompt}"`);

                const activeSystemPrompt = systemPrompt || SYSTEM_PROMPT;

                // -------------------------------------------------------
                // Out-of-scope keyword filter
                // -------------------------------------------------------
                const outOfScopeKeywords = [
                    'pricing', 'price', 'cost', 'rate', 'budget', 'quote',
                    'how much', 'charge', 'fee', 'pay', 'payment', 'invoice',
                    'ceo', 'founder', 'team', 'employee', 'staff', 'salary',
                    'revenue', 'funding', 'valuation', 'investor'
                ];

                const lowerPrompt = prompt.toLowerCase();

                const isOutOfScope = outOfScopeKeywords.some(k =>
                    lowerPrompt.includes(k)
                );

                if (isOutOfScope) {

                    const redirectReply =
                        "We don't share that information here. Please book a meeting with our engineering team at https://hashteelab.com";

                    saveAssistantReply(sessionId, redirectReply);

                    res.writeHead(200, { 'Content-Type': 'application/json' });

                    res.end(JSON.stringify({
                        reply: redirectReply,
                        sessionId
                    }));

                    return;
                }

                // -------------------------------------------------------
                // Build conversation history
                // -------------------------------------------------------
                const messages = buildMessages(sessionId, prompt);

                // -------------------------------------------------------
                // Call LLM via proxy
                // -------------------------------------------------------
                let reply = await callProxy(messages, activeSystemPrompt);

                // -------------------------------------------------------
                // Enforce concise replies
                // -------------------------------------------------------
                reply = reply.replace(/\n/g, ' ').trim();

                const sentences = reply.match(/[^.!?]+[.!?]+/g);

                if (sentences && sentences.length > 2) {
                    reply = sentences.slice(0, 2).join(' ').trim();
                }

                // Save reply
                saveAssistantReply(sessionId, reply);

                console.log(`[web-chat-api] reply="${reply.slice(0, 80)}..."`);

                res.writeHead(200, { 'Content-Type': 'application/json' });

                res.end(JSON.stringify({
                    reply,
                    sessionId
                }));

            } catch (err) {

                console.error('[web-chat-api] Error:', err.message);

                res.writeHead(500, { 'Content-Type': 'application/json' });

                res.end(JSON.stringify({
                    error: err.message
                }));
            }

        });

        return;
    }

    res.writeHead(404);
    res.end('Not found');

});


server.listen(API_PORT, '0.0.0.0', () => {

    console.log(`[web-chat-api] Running on http://localhost:${API_PORT}/query`);
    console.log(`[web-chat-api] Forwarding to proxy on port ${PROXY_PORT}`);

});
