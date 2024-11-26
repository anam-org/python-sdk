# Getting Started

Make sure you have followed the steps in the [Installation](../home/installation.md) guide.
This guide will help you get up and running with the Anam Python SDK.

## Basic Usage

Here's a simple example of how to use the AnamClient. We recommend to store your API credentials in a `.env` file in your project root directory.

When you start, you can use the `get_persona_presets()` method to see the different persona presets you can use to create your own personas.

```python
from anam_python_sdk.api.client import AnamClient
from dotenv import dotenv_values

# Load configuration from .env file
api_cfg = dotenv_values(".env")

# Create an AnamClient instance
client = AnamClient(cfg=api_cfg)

# Get persona presets
persona_presets = client.get_persona_presets()
print("Persona Presets:", persona_presets)

# Get existing personas
personas = client.get_personas()
print("Personas:", personas)
```

**Next Step**

Let's build your first persona [over here](../user-guide/creating-personas.md).