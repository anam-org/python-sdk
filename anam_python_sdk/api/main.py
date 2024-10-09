"""Main module for the AnamLab client application."""

from typing import Dict, Optional

from dotenv import dotenv_values
from anam_python_sdk.api.clients import AnamClient
from anam_python_sdk.api.personas.max import cfg as maxcfg
from anam_python_sdk.api.personas.christian import cfg as christiancfg
from anam_python_sdk.api.personas.justice import cfg as justicecfg
from anam_python_sdk.api.personas.kai import cfg as kaicfg
from anam_python_sdk.api.personas.leo import cfg as leocfg
from anam_python_sdk.api.personas.mina import cfg as minacfg


def print_persona_presets(client: AnamClient):
    persona_presets = client.get_persona_presets()
    if persona_presets is not None:
        print("Persona Presets:", persona_presets)

def print_personas(client: AnamClient):
    personas = client.get_personas()
    if personas is not None:
        print("Personas:", personas)

def print_persona_details(client: AnamClient, name: str):
    matching_personas = client.get_persona_by_name(name)
    if matching_personas:
        print(f"Matching personas for '{name}':")
        for persona in matching_personas:
            print(persona)

def main():
    api_cfg: Dict[str, Optional[str]] = dotenv_values(".env")
    client = AnamClient(cfg=api_cfg)

    # print(joshcfg)

    # leo = client.create_persona(leocfg)
    # print(leo)
    # mina = client.create_persona(minacfg)

    # print(mina)
    client.update_persona(leocfg)
    # client.update_persona(minacfg)

    # Get Presets
    # print_persona_presets(client)

    # Get personas
    # print_personas(client)

    # Get persona details
    # personas = ["Kai", "Christian", "Eva", "Justice", "Max"]
    # for name in personas:

    #    print_persona_details(client, name)

    # personas = [
    #     maxcfg,
    #     christiancfg,
    #     justicecfg,
    #     kaicfg,
    #     joshcfg
    # ]
    
    # for p in personas:
    #     print(f"Updating {p.name}")
    #     client.update_persona(p)

    # Print personas
    



if __name__ == "__main__":
    main()