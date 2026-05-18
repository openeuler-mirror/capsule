# Installing Deep Research

Install this skill into the local skills directory discovered by your agent runtime, then bootstrap it there.

## Prerequisites

- Git
- Python 3 available in the user's environment
- pip

If Python, Git or pip is missing, stop and ask the user to install them first.

## Installation

Tell the user that the whole process will take approximately 5-15 minutes, ask them not to worry/be impatient.

1. **Choose local skills directory.**

Use the directory where you discover local skills. Create a folder named `deep_research` inside that directory.

Examples:
- If you are OpenClaw: `~/.openclaw/workspace/skills/deep_research`
- If you are Codex: `~/.codex/skills/deep_research`
- If you are Claude Code: `~/.claude/skills/deep_research`
- If you are OpenCode: `~/.config/opencode/skills/deep_research`
- You may also discover the local skills directory from shell startup files such as `~/.bashrc`, `~/.zshrc`, `~/.bash_profile`, or `~/.profile`.

In the commands below, replace `<SKILLS_DIR>` with that directory path.

2. **Clone the repository to a temporary local directory:**
   ```bash
   tmp_dir="$(mktemp -d)"
   git clone -b master --single-branch https://gitcode.com/openeuler/capsule.git "$tmp_dir/aios"
   ```

   **Windows (PowerShell):**
   ```powershell
   $TmpDir = Join-Path $env:TEMP ("aios-" + [guid]::NewGuid().ToString())
   git clone -b master --single-branch https://gitcode.com/openeuler/capsule.git (Join-Path $TmpDir "aios")
   ```

3. **Export the clean skill package into the agent's local skills directory:**
   ```bash
   python3 "$tmp_dir/aios/application/slidea/scripts/export_skill.py" \
     --target "<SKILLS_DIR>/deep_research" \
     --manifest "skill/deep_research/manifest.json"
   ```

   **Windows (PowerShell):**
   ```powershell
   python (Join-Path $TmpDir "aios\application\slidea\scripts\export_skill.py") `
     --target "<SKILLS_DIR>\deep_research" `
     --manifest "skill/deep_research/manifest.json"
   ```

4. **Delete the cloned repository after the copy is complete:**
   ```bash
   rm -rf "$tmp_dir"
   ```

   **Windows (PowerShell):**
   ```powershell
   Remove-Item -Path $TmpDir -Recurse -Force
   ```

5. **Switch to the installed skill directory:**
   ```bash
   cd "<SKILLS_DIR>/deep_research"
   ```

   **Windows (PowerShell):**
   ```powershell
   Set-Location "<SKILLS_DIR>\deep_research"
   ```

6. **Run the installer in the installed skill directory:**

   **After each step is completed, or at least every 30 seconds, report the installation progress to the user so they do not think the script has failed.**

   ```bash
   python3 scripts/install/install.py --skip-playwright --skip-libreoffice
   ```

   **Windows (PowerShell):**
   ```powershell
   python scripts/install/install.py --skip-playwright --skip-libreoffice
   ```

7. **What the installer does:**
   - Detects `uv` and installs it when missing
   - Creates `.venv` with `uv venv --python 3.11 --seed`
   - Installs `requirements.txt`
   - Creates `.env` from `.env.example` when needed
   - Writes `SETUP_COMPLETED=true` after the base Python/bootstrap dependencies are installed

8. **Try to populate the default LLM settings and Tavily search keys in `.env`.**

   Try to reuse an LLM configuration that the user is already using locally, and fill these minimum mandatory fields in `<SKILLS_DIR>/deep_research/.env`:

   ```env
   DEFAULT_LLM_MODEL=
   DEFAULT_LLM_API_KEY=
   DEFAULT_LLM_API_BASE_URL=
   ```

   Deep research will not work properly until these values are configured.

   These settings currently support OpenAI-compatible APIs only.

   Good places to look include common local agent/runtime config files such as:
   - `~/.config/opencode/opencode.json`
   - `~/.openclaw/openclaw.json`
   - `~/.codex/config.toml`

   Before writing any discovered values into `.env`, first verify that the model, API key, and base URL actually work together by running a small `curl` test against the candidate OpenAI-compatible endpoint.

   A verification command can look like this:

   ```bash
   curl -sS "<BASE_URL>/chat/completions" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer <API_KEY>" \
     -d '{
       "model": "<MODEL>",
       "messages": [{"role": "user", "content": "hello"}],
       "max_tokens": 1
     }'
   ```

   If `curl` is not available in the user's environment, use another HTTP tool that is already available locally and can send an equivalent request.

   Only write the values into `.env` if this validation clearly succeeds and the response shows that the endpoint is usable.

   After the default LLM settings are handled, also try to auto-discover Tavily search keys locally and write them into `<SKILLS_DIR>/deep_research/.env`.

   Look for either `TAVILY_API_KEY` or `TAVILY_API_KEYS` in common local config and env files, especially places such as:
   - `~/.config/opencode/opencode.json`
   - `~/.openclaw/openclaw.json`
   - `~/.codex/config.toml`
   - shell startup files such as `~/.bashrc`, `~/.zshrc`, `~/.bash_profile`, or `~/.profile`

   When you find Tavily credentials, write them into `<SKILLS_DIR>/deep_research/.env` by strictly following the comments immediately above `TAVILY_API_KEYS`.

   Do not guess or fabricate Tavily keys. If you cannot find a reliable local value, leave `TAVILY_API_KEYS='[]'` as-is and explicitly tell the user that web search will be skipped until they provide a Tavily key.

   Optional items:

   - `TAVILY_API_KEYS`: recommended for web search; leaving it empty may cause content hallucinations
   - `EMBEDDING_MODEL` / `EMBEDDING_API_KEY` / `EMBEDDING_API_BASE_URL`: optional, used for embedding-based retrieval in deep research ranking
   - `DISABLE_EMBEDDING`: set to `true` to disable embedding-based retrieval

   If you cannot find a reliable OpenAI-compatible configuration locally, do not guess. Leave the values empty and clearly tell the user that they still need to fill in the three `DEFAULT_LLM_*` settings manually.

   You can also tell the user that they may send you the configuration and you can help fill it in, or they can edit `<SKILLS_DIR>/deep_research/.env` manually. After the configuration is updated, they should restart the agent so the skill can take effect.

## Verify

Check `<SKILLS_DIR>/deep_research/.env`.

- If `.env` does not exist, installation is not complete.
- If `SETUP_COMPLETED` is not `true`, installation is not complete.
- If `SETUP_COMPLETED=true`, treat the base Python/bootstrap dependencies as complete.

## Report Result

After installation work is finished, explicitly report the result to the user in summary block.
Summary block must reply in the same language the user is currently using。
Inside that final summary block, keep the wording concise and easy to scan. Cover these points:

1. **Installation result**
   - Clearly say whether the base installation completed successfully.
   - If there are remaining manual steps, say that the skill is not fully ready yet.

2. **Extra settings already applied**
   - If you helped the user auto-find and write the default LLM settings, say so briefly.
   - If you helped the user auto-find and write `TAVILY_API_KEYS`, say so briefly.
   - If you did not actually help configure one of these items, do not mention it as completed.

3. **Optional settings the user may still configure**
   - Mention only the items that are still relevant after the work you completed.
   - Optional items:
     - `TAVILY_API_KEYS`: recommended for web search; leaving it empty may cause content hallucinations
     - `EMBEDDING_MODEL` / `EMBEDDING_API_KEY` / `EMBEDDING_API_BASE_URL`: optional, used for embedding-based retrieval in deep research ranking
   - If you already configured Tavily for the user, do not repeat `TAVILY_API_KEYS` in this optional list.

4. **Required reminders**
   - If you did not help auto-configure the default LLM settings, explicitly tell the user that they still need to fill in the three `DEFAULT_LLM_*` values.

5. **What the user should do next**
   - Ask the user to send their LLM API key / base URL / model information if they want help filling the remaining `.env` settings.
   - Offer to help them finish any remaining optional configuration.

During the whole installation flow, including progress updates, warnings, final result summaries, and follow-up questions, always reply in the same language the user is currently using.