# Getting Started 
This client uses [Poetry](https://python-poetry.org/) for Python package & dependency management. 

## Installation (Package)
```zsh
    pip install anam-python-sdk
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