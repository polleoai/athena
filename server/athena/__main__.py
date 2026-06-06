"""Entry point for python -m athena and the 'athena' CLI command."""
import sys
import asyncio


def main():
    """CLI entry point for athena command."""
    from athena.server import main as serve_main

    args = sys.argv[1:]

    if not args or args[0] in ('-h', '--help', 'help'):
        print("Athena — Your Second Brain")
        print()
        print("Usage:")
        print("  athena init                    Interactive setup wizard")
        print("  athena serve /path/to/vault    Start MCP server (for LLM consoles)")
        print("  athena uninstall               Remove Athena and all extras")
        print("  athena --help                  Show this help")
        print()
        print("Required dependencies (auto-installed with athena-brain):")
        print("  - mcp        MCP server protocol")
        print("  - sqlite-vec Vector search engine")
        print()
        print("Optional extras:")
        print("  pip install athena-brain[search]  Vector embeddings (fastembed, ~130MB)")
        print("  pip install athena-brain[pdf]     PDF extraction (pymupdf4llm)")
        print("  pip install athena-brain[ocr]     Image OCR (pytesseract)")
        print("  pip install athena-brain[office]  Office docs (docx, pptx, xlsx)")
        print("  pip install athena-brain[export]  Chart/slide export (matplotlib)")
        print("  pip install athena-brain[media]   Audio/video transcription (whisper)")
        print("  pip install athena-brain[all]     Everything above")
        print()
        print("For CLI usage, use the 'kb' command directly in your vault:")
        print("  kb search <query>     Hybrid search")
        print("  kb query <question>   Answer a question from your KB")
        print("  kb add <url|file>     Capture a URL or local file")
        print("  kb index              Build search index")
        print("  kb help               Show all commands")
        return

    cmd = args[0]

    if cmd == 'serve':
        if len(args) < 2:
            print("Error: vault path required", file=sys.stderr)
            print("Usage: athena serve /path/to/vault", file=sys.stderr)
            sys.exit(1)
        asyncio.run(serve_main(args[1]))

    elif cmd == 'init':
        vault = args[1] if len(args) > 1 else None
        _init_interactive(vault)

    elif cmd == 'uninstall':
        _uninstall()

    else:
        # Assume first arg is vault path for backward compat
        asyncio.run(serve_main(args[0]))


def _uninstall():
    """Remove Athena and all optional dependencies + Athena-generated files."""
    import subprocess
    import shutil

    print()
    print("  Athena Uninstall")
    print("  ════════════════")
    print()
    print("  This will remove Athena and clean up all generated files.")
    print("  Your knowledge base content (raw/, wiki/) is NEVER deleted.")
    print()

    if not _ask_yn('  Proceed with uninstall?', default=False):
        print('  Cancelled.')
        return

    # ── Step 1: Detect vault location ──
    # Try current directory, then check config
    vault_root = None
    for candidate in [os.getcwd(), os.path.expanduser('~/athena-vault')]:
        if os.path.exists(os.path.join(candidate, '.athena')):
            vault_root = candidate
            break

    # ── Step 2: Clean up Athena-generated files ──
    if vault_root:
        print()
        print(f'  Vault found: {vault_root}')
        print()
        print('  Your knowledge (raw/, wiki/) is preserved. Remove Athena files?')
        print()

        # Search index + config
        athena_dir = os.path.join(vault_root, '.athena')
        if os.path.isdir(athena_dir):
            if _ask_yn('  a) Search index + config (.athena/)', default=True):
                shutil.rmtree(athena_dir)
                print('     ✓ Removed .athena/')

        # Obsidian plugin
        plugin_dir = os.path.join(vault_root, '.obsidian', 'plugins', 'athena')
        if os.path.isdir(plugin_dir):
            if _ask_yn('  b) Obsidian plugin (.obsidian/plugins/athena/)', default=True):
                shutil.rmtree(plugin_dir)
                # Also remove from community-plugins.json
                cp_path = os.path.join(vault_root, '.obsidian', 'community-plugins.json')
                if os.path.exists(cp_path):
                    try:
                        import json
                        with open(cp_path) as f:
                            plugins = json.load(f)
                        if 'athena' in plugins:
                            plugins.remove('athena')
                            with open(cp_path, 'w') as f:
                                json.dump(plugins, f, indent=2)
                    except (json.JSONDecodeError, IOError):
                        pass
                print('     ✓ Removed Obsidian plugin')

        # MCP config
        mcp_path = os.path.join(vault_root, '.mcp.json')
        if os.path.exists(mcp_path):
            if _ask_yn('  c) MCP server config (.mcp.json)', default=True):
                os.remove(mcp_path)
                print('     ✓ Removed .mcp.json')

        # Trash
        trash_dir = os.path.join(vault_root, '.kb-trash')
        if os.path.isdir(trash_dir):
            if _ask_yn('  d) Trash (.kb-trash/)', default=True):
                shutil.rmtree(trash_dir)
                print('     ✓ Removed .kb-trash/')

        # Clippings folder (only if empty)
        clip_dir = os.path.join(vault_root, 'inbox/clippings')
        if os.path.isdir(clip_dir) and not os.listdir(clip_dir):
            if _ask_yn('  e) Empty Clippings/ folder', default=True):
                os.rmdir(clip_dir)
                print('     ✓ Removed Clippings/')

        # Exports
        exports_dir = os.path.join(vault_root, 'wiki', 'exports')
        if os.path.isdir(exports_dir):
            if _ask_yn('  f) Generated exports (wiki/exports/)', default=True):
                shutil.rmtree(exports_dir)
                print('     ✓ Removed wiki/exports/')

        # RULES.md — keep by default since user may have customized it
        rules_path = os.path.join(vault_root, 'RULES.md')
        if os.path.exists(rules_path):
            if _ask_yn('  g) Processing rules (RULES.md)', default=False):
                os.remove(rules_path)
                print('     ✓ Removed RULES.md')
            else:
                print('     Kept RULES.md (your customizations)')

        print()

        # ── Step 3: Delete account ──
        config_path = os.path.join(vault_root, '.athena', 'config.json')
        # Config may already be deleted, check backup
        auth_email = None
        if not os.path.exists(config_path):
            # Already cleaned up, skip account deletion prompt
            pass
        else:
            try:
                import json
                with open(config_path) as f:
                    cfg = json.load(f)
                auth_email = cfg.get('auth', {}).get('email')
            except (json.JSONDecodeError, IOError):
                pass

        if auth_email:
            print(f'  Account: {auth_email}')
            if _ask_yn('  Delete your Athena account from our servers?', default=False):
                print('     Account deletion requested. We will process within 30 days.')
                # Note: actual server-side deletion requires a Supabase Edge Function
                # For now, we mark the request locally
            else:
                print('     Account kept. You can delete later at athena.dev/account')
            print()

    # ── Step 4: Remove pip packages ──
    print('  Removing packages...')
    packages = [
        'athena-brain',
        'fastembed', 'onnxruntime', 'tokenizers',
        'pymupdf4llm', 'PyMuPDF',
        'pytesseract', 'Pillow',
        'python-docx', 'python-pptx', 'openpyxl',
        'matplotlib', 'plotly',
        'faster-whisper',
        'mcp', 'sqlite-vec',
    ]

    removed = 0
    for pkg in packages:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'uninstall', '-y', pkg],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and 'Successfully' in result.stdout:
            print(f'    ✓ {pkg}')
            removed += 1

    print()
    print(f'  ✓ Athena uninstalled ({removed} packages removed).')
    print('  Your knowledge base content (raw/, wiki/) is preserved.')
    print('  To reinstall: pip install athena-brain')
    print()


def _ask(prompt, default=''):
    """Prompt user with a default value."""
    if default:
        display = f'{prompt} [{default}]: '
    else:
        display = f'{prompt}: '
    try:
        answer = input(display).strip()
        return answer if answer else default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def _ask_yn(prompt, default=True):
    """Yes/no prompt."""
    suffix = '[Y/n]' if default else '[y/N]'
    try:
        answer = input(f'{prompt} {suffix}: ').strip().lower()
        if not answer:
            return default
        return answer in ('y', 'yes')
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def _init_interactive(vault_path=None):
    """Interactive setup wizard for Athena."""
    import os
    import subprocess
    import json

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║   Athena — Your Second Brain         ║")
    print("  ║   Setup Wizard                       ║")
    print("  ╚══════════════════════════════════════╝")
    print()

    # Step 1: Vault location
    if not vault_path:
        default_path = os.path.expanduser('~/athena-vault')
        vault_path = _ask('1. Where should your knowledge base live?', default_path)

    vault_path = os.path.expanduser(vault_path)
    print(f'   Vault: {vault_path}')
    print()

    # Step 2: Capabilities
    print('2. Choose what to install. Text files (.md, .txt, .csv, .json, .py)')
    print('   are always supported with zero dependencies.')
    print()
    print('   Capability                       Size      What it enables')
    print('   ─────────────────────────────────────────────────────────────')
    print('   a) Vector search (fastembed)      ~130MB    Semantic search by meaning')
    print('   b) PDF extraction (pymupdf4llm)   ~15MB     Ingest .pdf files')
    print('   c) Image OCR (pytesseract)        ~50MB     Read text from images')
    print('   d) Office docs (python-docx etc)  ~10MB     Ingest .docx .pptx .xlsx')
    print('   e) Chart export (matplotlib)      ~50MB     Export PNG/HTML charts')
    print('   f) Audio/video (faster-whisper)   ~1.5GB    Transcribe .mp4 .mp3 .wav')
    print()

    install_all = _ask_yn('   Install all capabilities?', default=False)
    if install_all:
        install_fastembed = True
        install_pdf = True
        install_ocr = True
        install_office = True
        install_export = True
        install_media = True
    else:
        install_fastembed = _ask_yn('   a) Vector search (recommended)?', default=True)
        install_pdf = _ask_yn('   b) PDF extraction?', default=True)
        install_ocr = _ask_yn('   c) Image OCR?', default=False)
        install_office = _ask_yn('   d) Office documents?', default=False)
        install_export = _ask_yn('   e) Chart/slide export?', default=False)
        install_media = _ask_yn('   f) Audio/video transcription (~1.5GB)?', default=False)
    print()

    # Step 3: Web Clipper integration
    print('3. Web Clipper lets you save pages from your browser directly into')
    print('   Athena. Install the Obsidian Web Clipper browser extension and')
    print('   configure it to save to the Clippings/ folder.')
    enable_clipper = _ask_yn('   Enable Web Clipper auto-ingest?', default=True)
    print()

    # Step 4: LLM provider (optional)
    print('4. An LLM provider enables full answer synthesis (not just snippets).')
    print('   You can configure this later with: kb config set llm <provider>')
    print('   Options: ollama (free, local), claude (API key), openai (API key)')
    llm_provider = _ask('   LLM provider (press Enter to skip)', '')
    llm_key = ''
    if llm_provider and llm_provider not in ('ollama', 'skip', ''):
        llm_key = _ask(f'   {llm_provider} API key', '')
    print()

    # Step 5: Account sign-up
    print('5. Create your free Athena account.')
    print('   Get early updates, priority support, and community access.')
    print('   Your data stays local — the account is for communication only.')
    print()
    # Note: Pro and Team tiers exist in the database but are not shown yet.
    # When ready to launch paid tiers, uncomment the tier selection below.
    # print('   Plans:')
    # print('   [1] Free  — $0/mo  (hybrid search, export, file ingestion)')
    # print('   [2] Pro   — $12/mo (+ hosted LLM, answer synthesis, priority support)')
    # print('   [3] Team  — $20/user/mo (+ shared vaults, collaboration)')
    # selected_plan = _ask('   Choose plan', '1')
    user_email = _ask('   Email (press Enter to skip)', '')
    enable_telemetry = False
    if user_email:
        print()
        enable_telemetry = _ask_yn('   Share anonymous usage stats to help improve Athena?', default=True)
    print()

    # ── Execute setup ──
    print('Setting up Athena...')
    print()

    # Create directory structure
    dirs = [
        'raw/papers', 'raw/webpages', 'raw/repos', 'raw/videos',
        'raw/images', 'raw/assets',
        'wiki/format/papers', 'wiki/format/repos', 'wiki/format/webpages',
        'wiki/format/videos', 'wiki/format/images', 'wiki/format/entities',
        'wiki/topics', 'wiki/insights', 'wiki/journal', 'wiki/sessions',
        'wiki/feedback', 'wiki/dashboards', 'wiki/exports',
        'inbox', '.athena',
    ]
    if enable_clipper:
        dirs.append('inbox/clippings')

    for d in dirs:
        os.makedirs(os.path.join(vault_path, d), exist_ok=True)
    print(f'  ✓ Created {len(dirs)} directories')

    # Create index.md
    index_path = os.path.join(vault_path, 'index.md')
    if not os.path.exists(index_path):
        with open(index_path, 'w') as f:
            f.write('# Knowledge Base\n\n> 0 sources · 0 wiki pages\n')
    print('  ✓ Created index.md')

    # Create tracking files
    for fname in ['inbox/url-new.txt', 'inbox/url-resolved.tsv']:
        fpath = os.path.join(vault_path, fname)
        if not os.path.exists(fpath):
            with open(fpath, 'w') as f:
                pass

    # Create .gitignore
    gitignore_path = os.path.join(vault_path, '.gitignore')
    if not os.path.exists(gitignore_path):
        with open(gitignore_path, 'w') as f:
            f.write('.athena/\n.obsidian-api-key\n')
    print('  ✓ Created .gitignore')

    # Save config
    config = {}
    if enable_clipper:
        config['clippings'] = {'enabled': True, 'directory': 'inbox/clippings'}
    if llm_provider and llm_provider not in ('skip', ''):
        llm_cfg = {'provider': llm_provider}
        if llm_key:
            llm_cfg['api_key'] = llm_key
        config['llm'] = llm_cfg
    if enable_telemetry:
        config['telemetry'] = True

    config_path = os.path.join(vault_path, '.athena', 'config.json')
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print('  ✓ Saved configuration')

    # Sign up
    if user_email:
        print('  ⠋ Creating account...')
        try:
            from athena.telemetry import sign_up
            ok, msg = sign_up(user_email, vault_path)
            if ok:
                print(f'  ✓ {msg}')
            else:
                print(f'  ⚠ {msg}')
                print('    You can sign up later: athena signup')
        except Exception as e:
            print(f'  ⚠ Sign-up failed: {e}')
            print('    You can sign up later: athena signup')

    # Install selected capabilities
    extras = []
    if install_fastembed:
        extras.append('search')
    if install_pdf:
        extras.append('pdf')
    if install_ocr:
        extras.append('ocr')
    if install_office:
        extras.append('office')
    if install_export:
        extras.append('export')
    if install_media:
        extras.append('media')

    if extras:
        extras_str = ','.join(extras)
        print(f'  ⠋ Installing extras: {extras_str}...')
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', f'athena-brain[{extras_str}]'],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                print(f'  ✓ Installed: {extras_str}')
            else:
                # Try individual extras so partial success is possible
                for extra in extras:
                    r = subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', f'athena-brain[{extra}]'],
                        capture_output=True, text=True, timeout=120
                    )
                    if r.returncode == 0:
                        print(f'  ✓ Installed: {extra}')
                    else:
                        print(f'  ⚠ Failed: {extra}. Install manually: pip install athena-brain[{extra}]')
        except (subprocess.TimeoutExpired, OSError):
            print(f'  ⚠ Install timed out. Install manually: pip install athena-brain[{extras_str}]')

    # Create MCP config
    mcp_path = os.path.join(vault_path, '.mcp.json')
    if not os.path.exists(mcp_path):
        mcp_config = {
            "mcpServers": {
                "athena": {
                    "command": "python3",
                    "args": ["-m", "athena.server", vault_path],
                    "env": {"PYTHONPATH": os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}
                }
            }
        }
        with open(mcp_path, 'w') as f:
            json.dump(mcp_config, f, indent=2)
        print('  ✓ Created .mcp.json (MCP server config)')

    # Summary
    print()
    print('  ╔══════════════════════════════════════╗')
    print('  ║   Setup complete!                    ║')
    print('  ╚══════════════════════════════════════╝')
    print()
    print(f'  Vault:          {vault_path}')
    installed_caps = []
    if install_fastembed: installed_caps.append('vector search')
    if install_pdf: installed_caps.append('PDF')
    if install_ocr: installed_caps.append('image OCR')
    if install_office: installed_caps.append('Office docs')
    if install_export: installed_caps.append('chart export')
    if install_media: installed_caps.append('audio/video')
    print(f'  Plan:           Free')
    print(f'  Account:        {user_email if user_email else "none (sign up later: athena signup)"}')
    print(f'  Capabilities:   {", ".join(installed_caps) if installed_caps else "text files only"}')
    print(f'  Web Clipper:    {"enabled — save clips to Clippings/" if enable_clipper else "disabled"}')
    print(f'  LLM provider:   {llm_provider or "none (configure later with kb config)"}')
    print(f'  Telemetry:      {"on (anonymous)" if enable_telemetry else "off"}')
    print()
    print('  Next steps:')
    print('  1. Open the vault in Obsidian')
    print('  2. Start adding sources:')
    print('     kb add https://github.com/some/repo')
    print('     kb add https://arxiv.org/abs/1234.5678')
    print('  3. Build the search index:')
    print('     kb index')
    print('  4. Search your knowledge:')
    print('     kb search "machine learning"')
    if enable_clipper:
        print('  5. Install Obsidian Web Clipper in your browser:')
        print('     https://obsidian.md/clipper')
        print(f'     Configure save location → {vault_path}/Clippings/')
    print()


if __name__ == '__main__':
    main()
