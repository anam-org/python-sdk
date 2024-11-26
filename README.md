# Anam Python SDK

Welcome to the Anam Python SDK! This SDK provides tools for creating and managing virtual personas using the AnamLab platform.

## Features

- Create and manage virtual personas
- Interact with the AnamLab platform
- Customize persona behaviors and responses

## Installation

To install the Anam Python SDK, use:

```bash
pip install anam-python-sdk
```

or

```bash
poetry add anam-python-sdk
```

## Getting Started

### Prerequisites

- Python 3.7 or higher
- pip (Python package installer)

### Configuration

1. Create a `.env` file in your project root directory.
2. Add your API credentials to the `.env` file:

```bash
ANAM_API_KEY=<your_api_key>
```

### Basic Usage
Here's a simple example of how to use the AnamClient:

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

### Creating a Persona

For more detailed information on using the SDK, check out the [User Guide](docs/user-guide/creating-personas.md).

## Documentation

For comprehensive documentation, please refer to the following sections:

1. [Getting Started](docs/getting-started.md)
2. [User Guide](docs/user-guide/creating-personas.md)
3. [API Reference](docs/api-reference/model.md)
4. [Examples](docs/examples.md)

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.