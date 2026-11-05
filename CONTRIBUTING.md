# Contributing

1. Create a branch from `main`.
2. Install the development environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

3. Make your change and add tests.
4. Run the checks:

   ```bash
   ruff check .
   ruff format --check .
   pytest
   ```

5. Open a pull request describing the change and any algorithm trade-offs.

## Guidelines

- Keep the optimization services dependency-free apart from Pydantic models.
- Add a test for every bug fix and every new constraint.
- Update the README when an API payload or response changes.
- Prefer clear, deterministic behaviour: seeded random numbers and stable sorting.
