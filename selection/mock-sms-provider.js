// Mock SMS provider: answers every send after a fixed delay, like an external gateway would.
//
//   npm install express
//   PORT=8080 DELAY_MS=500 JITTER_MS=0 node mock-sms-provider.js
//
//   POST /send    answers after DELAY_MS (± JITTER_MS)
//   GET  /stats   counters and observed latency
//   POST /reset   clears the counters

const express = require('express');
const app = express();
app.use(express.json());

const PORT = parseInt(process.env.PORT || '8080', 10);
const DELAY_MS = parseInt(process.env.DELAY_MS || '500', 10);
const JITTER_MS = parseInt(process.env.JITTER_MS || '0', 10);

let stats = {
  totalRequests: 0,
  totalLatencyMs: 0,
  minLatencyMs: null,
  maxLatencyMs: null,
  startedAt: new Date().toISOString(),
};

function randomDelay() {
  if (JITTER_MS <= 0) return DELAY_MS;
  const offset = Math.floor((Math.random() * 2 - 1) * JITTER_MS);
  return Math.max(0, DELAY_MS + offset);
}

app.post('/send', (req, res) => {
  const start = Date.now();
  const delay = randomDelay();

  setTimeout(() => {
    const elapsed = Date.now() - start;

    stats.totalRequests += 1;
    stats.totalLatencyMs += elapsed;
    stats.minLatencyMs = stats.minLatencyMs === null ? elapsed : Math.min(stats.minLatencyMs, elapsed);
    stats.maxLatencyMs = stats.maxLatencyMs === null ? elapsed : Math.max(stats.maxLatencyMs, elapsed);

    res.status(200).json({
      status: 'sent',
      messageId: `mock-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
      to: req.body?.to || 'unknown',
      simulatedDelayMs: delay,
    });
  }, delay);
});

app.get('/stats', (req, res) => {
  const avg = stats.totalRequests > 0 ? (stats.totalLatencyMs / stats.totalRequests).toFixed(1) : null;
  res.status(200).json({
    ...stats,
    avgLatencyMs: avg,
    configuredDelayMs: DELAY_MS,
    configuredJitterMs: JITTER_MS,
  });
});

app.post('/reset', (req, res) => {
  stats = {
    totalRequests: 0,
    totalLatencyMs: 0,
    minLatencyMs: null,
    maxLatencyMs: null,
    startedAt: new Date().toISOString(),
  };
  res.status(200).json({ reset: true });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Mock SMS provider listening on port ${PORT}`);
  console.log(`Delay ${DELAY_MS} ms (jitter ±${JITTER_MS} ms)`);
});
