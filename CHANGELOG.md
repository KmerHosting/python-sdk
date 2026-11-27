# Changelog

## 0.4.0

- Automatic retries for 429, 502, 503 and 504 (3 attempts by default) with exponential backoff and jitter; `Retry-After` is honored.
- Mutating calls reuse the same `Idempotency-Key` on every attempt.
- New `RateLimitError` (subclass of `KmerHostingError`) with `retry_after`, `limit` and `remaining`.
- New client options `max_attempts`, `retry_base_delay`, `retry_max_delay`.
- OpenAPI copy updated with 429 responses.

## 0.3.1

- Public domain search and spec aligned with the live API.
