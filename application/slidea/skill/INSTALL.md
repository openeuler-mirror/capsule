# Installing Slidea

Install this skill into the local skills directory discovered by your agent runtime, then bootstrap it there.

## Prerequisites

- Git
- Python 3 available in the user's environment
- pip

If Python, Git or pip is missing, stop and ask the user to install them first.

## Installation

Tell the user that the whole process will take approximately 5-15 minutes, ask them not to worry/be impatient.

1. **Choose local skills directory.**

Use the directory where you discover local skills. Create a folder named `slidea` inside that directory.

Examples:
- If you are OpenClaw: `~/.openclaw/workspace/skills/slidea`
- If you are Codex: `~/.codex/skills/slidea`
- If you are Claude Code: `~/.claude/skills/slidea`
- If you are OpenCode: `~/.config/opencode/skills/slidea`
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
     --target "<SKILLS_DIR>/slidea"
   ```

   **Windows (PowerShell):**
   ```powershell
   python (Join-Path $TmpDir "aios\application\slidea\scripts\export_skill.py") `
     --target "<SKILLS_DIR>\slidea"
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
   cd "<SKILLS_DIR>/slidea"
   ```

   **Windows (PowerShell):**
   ```powershell
   Set-Location "<SKILLS_DIR>\slidea"
   ```

6. **Run the installer in the installed skill directory:**

   **After each step is completed, or at least every 30 seconds, report the installation progress to the user so they do not think the script has failed.**

   ```bash
   python3 scripts/install/install.py
   ```

   **Windows (PowerShell):**
   ```powershell
   python scripts/install/install.py
   ```

7. **What the installer does:**
   - Detects `uv` and installs it when missing
   - Creates `.venv` with `uv venv --python 3.11 --seed`
   - Installs `requirements.txt`
   - Installs Playwright Chromium
   - Detects whether a usable LibreOffice installation is already available
   - If LibreOffice is missing, installs a local copy on supported platforms, or prints manual installation guidance
   - On RHEL-family Linux x86_64 and arm64, follow the final `install.py` log output and explicitly tell the user to run `extra_install_linux_rhel.sh` manually when the installer asks for it
   - Creates `.env` from `.env.example` when needed
   - Writes `SETUP_COMPLETED=true` after the base Python/bootstrap dependencies are installed

8. **Try to populate the default LLM settings and Tavily search keys in `.env`.**

   Try to reuse an LLM configuration that the user is already using locally, and fill these three mandatory fields in `<SKILLS_DIR>/slidea/.env`:

   ```env
   DEFAULT_LLM_MODEL=
   DEFAULT_LLM_API_KEY=
   DEFAULT_LLM_API_BASE_URL=
   ```

   PPT generation will not work properly until these three values are configured.

   These three settings currently support OpenAI-compatible APIs only.

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

   After the default LLM settings are handled, also try to auto-discover Tavily search keys locally and write them into `<SKILLS_DIR>/slidea/.env`.

   Look for either `TAVILY_API_KEY` or `TAVILY_API_KEYS` in common local config and env files, especially places such as:
   - `~/.config/opencode/opencode.json`
   - `~/.openclaw/openclaw.json`
   - `~/.codex/config.toml`
   - shell startup files such as `~/.bashrc`, `~/.zshrc`, `~/.bash_profile`, or `~/.profile`

   When you find Tavily credentials, write them into `<SKILLS_DIR>/slidea/.env` by strictly following the comments immediately above `TAVILY_API_KEYS`.

   Do not guess or fabricate Tavily keys. If you cannot find a reliable local value, leave `TAVILY_API_KEYS='[]'` as-is and explicitly tell the user that web search and image search will be skipped until they provide a Tavily key.

   Optional items:

   - `TAVILY_API_KEYS`: recommended for web and image search; leaving it empty may cause content hallucinations and may result in a PPT without images
   - `DEFAULT_VLM_MODEL` / `DEFAULT_VLM_API_KEY` / `DEFAULT_VLM_API_BASE_URL`: optional, used to check layout after generation
   - `IMG_GEN_MODEL` / `IMG_GEN_API_KEY` / `IMG_GEN_API_BASE_URL`: optional, used to generate illustrations for the PPT

   If you cannot find a reliable OpenAI-compatible configuration locally, do not guess. Leave the values empty and clearly tell the user that they still need to fill in the three `DEFAULT_LLM_*` settings manually.

   You can also tell the user that they may send you the configuration and you can help fill it in, or they can edit `<SKILLS_DIR>/slidea/.env` manually. After the configuration is updated, they should restart the agent so the skill can take effect.

   Before finishing, you must explicitly tell the user which model families are recommended for better results: **Gemini 3.1 Pro / DeepSeek V3 / Kimi K2.5**.

## Verify

Check `<SKILLS_DIR>/slidea/.env`.

- If `.env` does not exist, installation is not complete.
- If `SETUP_COMPLETED` is not `true`, installation is not complete.
- If `SETUP_COMPLETED=true`, treat the base Python/bootstrap dependencies as complete.
- `SETUP_COMPLETED=true` does not guarantee PPTX export on every platform. PPTX export still requires a usable LibreOffice installation for the PDF-to-PPTX conversion step.

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
     - `TAVILY_API_KEYS`: recommended for web and image search; leaving it empty may cause content hallucinations and may result in a PPT without images
     - `DEFAULT_VLM_MODEL` / `DEFAULT_VLM_API_KEY` / `DEFAULT_VLM_API_BASE_URL`: optional, used to check layout after generation
     - `IMG_GEN_MODEL` / `IMG_GEN_API_KEY` / `IMG_GEN_API_BASE_URL`: optional, used to generate illustrations for the PPT
   - If you already configured Tavily for the user, do not repeat `TAVILY_API_KEYS` in this optional list.

4. **Required reminders**
   - If you did not help auto-configure the default LLM settings, explicitly tell the user that they still need to fill in the three `DEFAULT_LLM_*` values.
   - Even if the default LLM settings were already configured, still explicitly remind the user that the recommended model families are **Gemini 3.1 Pro / DeepSeek V3 / Kimi K2.5**.
   - If the platform is RHEL-family Linux, you must explicitly show the full command the user needs to run next. The command must be complete and directly copyable.

5. **What the user should do next**
   - Ask the user to send their LLM API key / base URL / model information if they want help filling the remaining `.env` settings.
   - Offer to help them finish any remaining optional configuration.

When the platform is RHEL-family Linux, the final summary block should include the exact command from the installer result, not an abbreviated filename-only reference.

During the whole installation flow, including progress updates, warnings, final result summaries, and follow-up questions, always reply in the same language the user is currently using.
