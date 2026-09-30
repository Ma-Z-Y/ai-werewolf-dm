import { copyFileSync, existsSync } from 'node:fs';
import { defineConfig, loadEnv, type Plugin } from 'vite';

/**
 * Dev-only LLM proxy. Local inference servers (e.g. MTPLX) refuse browser
 * cross-origin requests, so the browser calls `/__llm/<path>` on the Vite
 * server and the request is forwarded server-side to `<LLM_BASE_URL>/<path>`
 * (or to the `x-llm-base` header: the provider picked in 配置). A key sent by
 * the browser (from its encrypted vault) is passed through; otherwise
 * `LLM_API_KEY` from `.env` is added — only for the `.env` server itself, so it
 * never reaches the browser or any other host.
 */
function llmProxy(env: Record<string, string>): Plugin {
  return {
    name: 'llm-proxy',
    configureServer(server) {
      server.middlewares.use('/__llm', async (req, res) => {
        const base = String(req.headers['x-llm-base'] ?? env.LLM_BASE_URL ?? '').replace(/\/+$/, '');
        if (!/^https?:\/\//.test(base)) {
          res.statusCode = 400;
          res.end(JSON.stringify({ error: { message: 'LLM_BASE_URL 未配置（见 .env）' } }));
          return;
        }
        const chunks: Buffer[] = [];
        for await (const c of req) chunks.push(c as Buffer);
        const headers: Record<string, string> = { 'content-type': 'application/json' };
        const envBase = (env.LLM_BASE_URL ?? '').replace(/\/+$/, '');
        if (req.headers.authorization) headers.authorization = String(req.headers.authorization);
        else if (env.LLM_API_KEY && base === envBase) headers.authorization = `Bearer ${env.LLM_API_KEY}`;
        try {
          const upstream = await fetch(base + (req.url ?? ''), {
            method: req.method,
            headers,
            body: req.method === 'GET' || req.method === 'HEAD' ? undefined : Buffer.concat(chunks),
          });
          res.statusCode = upstream.status;
          res.setHeader('content-type', upstream.headers.get('content-type') ?? 'application/json');
          res.end(Buffer.from(await upstream.arrayBuffer()));
        } catch (e) {
          res.statusCode = 502;
          res.end(JSON.stringify({ error: { message: `proxy upstream error: ${(e as Error).message}` } }));
        }
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  // first run: seed .env from the committed template
  if (!existsSync('.env') && existsSync('.env.example')) copyFileSync('.env.example', '.env');
  const env = loadEnv(mode, process.cwd(), 'LLM_');
  // non-secret settings for the browser; the API key stays on the server
  const browserEnv = {
    LLM_BASE_URL: env.LLM_BASE_URL ?? '',
    LLM_MODELS: env.LLM_MODELS ?? '',
    LLM_MODEL: env.LLM_MODEL ?? '',
    LLM_REASONING: env.LLM_REASONING ?? '',
    LLM_DECISION_REASONING: env.LLM_DECISION_REASONING ?? '',
    LLM_USE_PROXY: env.LLM_USE_PROXY ?? '',
    LLM_TIMEOUT_MS: env.LLM_TIMEOUT_MS ?? '',
    LLM_HAS_KEY: env.LLM_API_KEY ? '1' : '',
  };
  return {
    plugins: [llmProxy(env)],
    define: { __LLM_ENV__: JSON.stringify(browserEnv) },
    // relative asset paths: the build works under any sub-path (GitHub Pages: /<repo>/)
    base: './',
    server: { port: 5173 },
    build: {
      rolldownOptions: {
        output: {
          codeSplitting: {
            groups: [
              { name: 'three', test: /node_modules[\\/]three/, minSize: 100_000, maxSize: 250_000, priority: 20 },
              { name: 'vendor', test: /node_modules/, minSize: 100_000, priority: 10 },
            ],
          },
        },
      },
    },
  };
});
