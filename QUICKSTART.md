# APRIL Quickstart

1. Configure the model:

   ```bash
   cp .env.example .env
   # edit OPENAI_API_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY
   ```

2. Verify the stack offline:

   ```bash
   python3 -m april
   ```

3. Run the Textual TUI:

   ```bash
   python3 -m april.cli.main
   ```

4. Run the API (for an external client):

   ```bash
   python3 -m april.cli.main --api
   ```

5. Run the tests:

   ```bash
   .venv/bin/pytest tests -q
   ```

The console script `april` is available after `pip install -e .`.
