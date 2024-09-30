# Getting Started

This guide will help you get up and running with the Anam Python SDK.

## Prerequisites

- Python 3.7 or higher
- pip (Python package installer)

## Installation

Install the Anam Python SDK using pip:

```bash
pip install anam-python-sdk
```

or using Poetry:

```bash
poetry add anam-python-sdk
```

## Configuration

1. Create a `.env` file in your project root directory.
2. Add your API credentials to the `.env` file:

```bash
ANAM_API_KEY=<your_api_key>
ANAM_API_SECRET=<your_api_secret>
```

## Basic Usage

Here's a simple example of how to use the AnamClient:

```python
from anam_python_sdk.api.clients import AnamClient
from dotenv import dotenv_values

# Load configuration from .env file
api_cfg = dotenv_values(".env")

# Create an AnamClient instance
client = AnamClient(cfg=api_cfg)

#Get persona presets
persona_presets = client.get_persona_presets()
print("Persona Presets:", persona_presets)

# Get existing personas
personas = client.get_personas()
print("Personas:", personas)
```

For more detailed information on using the SDK, check out the [User Guide](user-guide/creating-personas.md).