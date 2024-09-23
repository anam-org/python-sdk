# Using the AnamLabClient

The AnamLabClient is the main interface for interacting with the Anam Lab platform. This guide will show you how to use its various methods.

## Initialization

First, import the necessary modules and create an instance of the AnamLabClient:

```python
from anam_python_sdk.lab.client import AnamLabClient
from dotenv import dotenv_values

api_cfg = dotenv_values(".env")
client = AnamLabClient(cfg=api_cfg)
```

