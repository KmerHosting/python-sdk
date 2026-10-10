# kmerhosting-sdk

```bash
pip install kmerhosting-sdk
```

```python
from kmerhosting import KmerHostingClient

client = KmerHostingClient()  # reads KMERHOSTING_API_KEY
print(client.services.list())
```

This synchronous SDK uses Python's standard library and keeps API keys in memory only.

Source: https://github.com/KmerHosting/python-sdk
API documentation: https://api.kmerhosting.com/docs

License: Apache-2.0.
