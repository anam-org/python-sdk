# Contributing to anam-ai Python SDK

Thank you for your interest in contributing to the Anam AI Python SDK!

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/anam-org/python-sdk.git
   cd python-sdk
   ```

2. Install uv (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. Install dependencies:
   ```bash
   uv sync --dev
   ```

4. Run tests:
   ```bash
   uv run pytest -v
   ```

5. Run linting:
   ```bash
   uv run ruff check src/
   uv run ruff format --check src/
   uv run mypy src/
   ```

## Commit Message Convention

This project uses [Conventional Commits](https://www.conventionalcommits.org/) for automated versioning and changelog generation.

### Format

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types and Version Bumps

| Type | Description | Version Bump |
|------|-------------|--------------|
| `feat` | A new feature | Minor (0.x.0 → 0.y.0) |
| `fix` | A bug fix | Patch (0.0.x → 0.0.y) |
| `docs` | Documentation only changes | No release |
| `style` | Code style changes (formatting, etc.) | No release |
| `refactor` | Code change that neither fixes a bug nor adds a feature | No release |
| `perf` | Performance improvement | Patch |
| `test` | Adding or updating tests | No release |
| `chore` | Maintenance tasks | No release |
| `ci` | CI/CD changes | No release |

### Breaking Changes

For breaking changes, add `!` after the type or include `BREAKING CHANGE:` in the footer:

```
feat!: remove deprecated API methods

BREAKING CHANGE: The `old_method()` has been removed. Use `new_method()` instead.
```

While the project is on version 0.x, breaking changes bump the minor version.

### Examples

```bash
# Feature (bumps minor version)
git commit -m "feat: add support for custom TTS voices"

# Bug fix (bumps patch version)
git commit -m "fix: handle connection timeout gracefully"

# Documentation (no version bump)
git commit -m "docs: update API reference for streaming"

# Breaking change (bumps minor version while 0.x)
git commit -m "feat!: change event callback signature"

# With scope
git commit -m "fix(streaming): resolve audio sync issue"
```

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes with appropriate tests
3. Ensure all tests pass and linting is clean
4. Submit a PR with a clear description
5. Wait for review and CI checks to pass

## Release Process

- **Alpha releases**: Automatically triggered on every merge to `main` with conventional commits
- **Stable releases**: Manually triggered via GitHub Actions workflow

## Code Style

- Follow PEP 8 guidelines
- Use type hints for all public APIs
- Run `uv run ruff format src/` to auto-format code
- Ensure `uv run mypy src/` passes without errors
