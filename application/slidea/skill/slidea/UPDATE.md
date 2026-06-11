# Updating Slidea

When the Slidea skill code or dependencies change, follow this guide to update your local installation.

## Prerequisites

- Git
- Python 3 available in the user's environment
- pip

If Python, Git or pip is missing, stop and ask the user to install them first.

## Update Process

Tell the user that the whole process will take approximately 2-5 minutes, ask them not to worry/be impatient.

1. **Identify your Slidea installation directory.**

This is the `<SKILLS_DIR>/slidea` directory where you originally installed the skill.

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
     --target "<SKILLS_DIR>/slidea" --update
   ```

   **Windows (PowerShell):**
   ```powershell
   python (Join-Path $TmpDir "aios\application\slidea\scripts\export_skill.py")
   --target "<SKILLS_DIR>\slidea" --update
   ```

4. **Delete the cloned repository after the export is complete:**
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

6. **Run the update script:**

   **After each step is completed, or at least every 120 seconds, report the update progress to the user.**

   ```bash
   python3 scripts/install/update.py
   ```

   **Windows (PowerShell):**
   ```powershell
   python scripts/install/update.py
   ```

7. **What the update script does:**
   - Compares `requirements.txt` hash with stored state in `.install_state.json`
   - Reinstalls Python dependencies if `requirements.txt` has changed
   - Updates `.install_state.json` with new hashes

## What Gets Preserved

The following files and directories are preserved during update:

- `.env` - Your configuration with API keys and settings
- `.venv/` - Virtual environment (recreated only if missing)
- `.install_state.json` - Update state tracking

## What Gets Replaced

The following directories and files will be overwritten:

- `core/` - Main skill logic
- `scripts/` - Installation and utility scripts
- `docs/` - Documentation files
- `skill/` - Skill-specific files
- `requirements.txt` - Python dependencies
- `.env.example` - Configuration template
- `README.md`, `INSTALL.md`, `UPDATE.md` - Documentation

## Verify

After the update completes, check the output:

- If all steps show `[OK]`, the update was successful
- If any step shows `[FAILED]` or `[SKIPPED]`, check the error message and retry
- The `.install_state.json` file should reflect the new update timestamp
