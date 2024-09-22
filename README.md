# Getting Started 
This client uses [Poetry](https://python-poetry.org/) for Python package & dependency management. 

## Backlog 
- Core API 
    - Establishing a P2P Websocket to the Anam engine
    - Configuring the API with a Pythonic interface
    - Plug and play with 
        - Gradio, Streamlit or FastHTML
        - Fast API
- Character Dev
    - Create 3 to 4 personas
- Brains 
    - Creating a RAG tool call & establishing a `talk()` command
    - Langchain integration
- Persona API 
    - Configuring a new persona using the client (i.e. what you do in Anam Lab)
    - Selecting a persona 

## Installation (Package)
```zsh
    pip install anam-python-sdk
```
```python 
    from anamai.core import AnamClient
    from anamai.config import DEFAULT_FILTER_PHRASES_EN
    from dotenv import dotenv_values

    ENV = dotenv_values(".env.example")

    anc = AnamClient(ENV)

    eva = anc.Persona(
        personality = """
        You are role-playing as a text chatbot hotel receptionist at The Sunset Hotel. 
        Your name is Eva.
        """, 
        system_prompt="""
        You are role-playing as a text chatbot hotel receptionist at The Sunset Hotel. 
        Your name is Eva. Start with 'Hi, this is The Sunset Hotel reception, how may I help you?' 
        Be mindful of the hotel's availability: single and double rooms 
        are still available all next week, except Tuesday. 
        Dogs are allowed. There's a restaurant and bar in the lobby. 
        If communication breaks down, ask if they want to speak to a human. 
        Finish every response with a question to drive the conversation. 
        Do not repeat yourself.
        """, 
        filter_phrases=DEFAULT_FILTER_PHRASES_EN
    )




```

## Installation (Local)
*Using Conda*
1. Create a python environment `(^3.10)` in your top-level directory. 
    - `.conda/bin/python`
    - Ensure that its activated; i.e. `(.conda)` shows. 
2. Configure poetry to use `.conda/bin/python`: 
```zsh
    (.conda) poetry config virtualenvs.path $CONDA_ENV_PATH
    (.conda) poetry config virtualenvs.create false
    (.conda) poetry env use .conda/bin/python
```
3. Install the dependencies
```zsh
    (.conda) poetry install
```