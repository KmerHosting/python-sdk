# kmerhosting-sdk

```bash
pip install kmerhosting-sdk
```

```python
from kmerhosting import KmerHostingClient

client = KmerHostingClient()  # reads KMERHOSTING_API_KEY
print(client.services.list())
```

Check domain availability and price (public, no API key used, up to 20 domains, 20 searches per minute per IP):

```python
results = client.domains.search(["example.com", "example.cm"])["results"]
```

## Retries and rate limits

Requests that fail with `429`, `502`, `503` or `504` are retried automatically (3 attempts in total) with exponential backoff and jitter. `Retry-After` is honored, and a write keeps the **same** `Idempotency-Key` on every attempt. When attempts run out on a 429 the client raises `RateLimitError` (a subclass of `KmerHostingError`):

```python
from kmerhosting import KmerHostingClient, RateLimitError

client = KmerHostingClient(max_attempts=5)  # max_attempts=1 disables retries
try:
    client.services.list()
except RateLimitError as error:
    print(f"retry in {error.retry_after}s")
```

This synchronous SDK uses Python's standard library and keeps API keys in memory only.

Source: https://github.com/KmerHosting/python-sdk
API documentation: https://api.kmerhosting.com/docs

License: Apache-2.0.
