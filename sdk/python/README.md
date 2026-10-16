# noust-sdk

A typed Python client of the Noust API, generated from its OpenAPI schema. Standard library only,
Python 3.11 or later.

```python
from noust_sdk import Client

client = Client.from_environment()          # NOUST_URL and NOUST_TOKEN
for app in client.applications.list_apps()["apps"]:
    print(app["domain"], app["status"])
```

The methods and the types in `noust_sdk/_operations.py` and `noust_sdk/_models.py` are generated
(`python scripts/generate_sdk.py` in the Noust repository); everything else is written by hand. The
guide is `docs/sdk.md`.
