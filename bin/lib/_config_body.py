import sys, os, json

kb_root = sys.argv[1]
args = sys.argv[2:]
config_path = os.path.join(kb_root, '.athena', 'config.json')

def load_config():
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}

def save_config(cfg):
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(cfg, f, indent=2)
    print(f"Config saved: {config_path}")

if not args or args[0] == 'show':
    cfg = load_config()
    if not cfg:
        print("No configuration set.")
        print("  kb config set llm ollama        # use local Ollama")
        print("  kb config set llm claude --key KEY  # use Claude API")
        print("  kb config set llm openai --key KEY  # use OpenAI API")
    else:
        print("Athena Configuration:")
        for section, values in cfg.items():
            print(f"  [{section}]")
            if isinstance(values, dict):
                for k, v in values.items():
                    display_v = v if k != 'api_key' else v[:8] + '...' + v[-4:]
                    print(f"    {k}: {display_v}")
            else:
                print(f"    {values}")

elif args[0] == 'set' and len(args) >= 3:
    section = args[1]
    value = args[2]
    cfg = load_config()

    if section == 'llm':
        provider = value
        if provider not in ('ollama', 'claude', 'openai', 'gemini'):
            print(f"Unknown LLM provider: {provider}")
            print("Supported: ollama, claude, openai, gemini")
            sys.exit(1)
        llm_cfg = {'provider': provider}
        # Parse --key flag
        for i, a in enumerate(args):
            if a == '--key' and i + 1 < len(args):
                llm_cfg['api_key'] = args[i + 1]
        if provider != 'ollama' and 'api_key' not in llm_cfg:
            print(f"API key required for {provider}: kb config set llm {provider} --key YOUR_KEY")
            sys.exit(1)
        cfg['llm'] = llm_cfg
        save_config(cfg)
        print(f"LLM provider set to: {provider}")
    else:
        cfg.setdefault(section, {})[value] = args[3] if len(args) > 3 else True
        save_config(cfg)

elif args[0] == 'unset' and len(args) >= 2:
    cfg = load_config()
    section = args[1]
    if section in cfg:
        del cfg[section]
        save_config(cfg)
        print(f"Removed: {section}")
    else:
        print(f"Not set: {section}")

else:
    print("Usage:")
    print("  kb config                         # show current config")
    print("  kb config set llm ollama          # use local Ollama for QA synthesis")
    print("  kb config set llm claude --key KEY # use Claude API")
    print("  kb config set llm openai --key KEY # use OpenAI API")
    print("  kb config unset llm               # remove LLM config")