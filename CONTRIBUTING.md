# Contributing

## Set up

Use Python 3.12+ and an isolated environment. From the checkout:

```bash
python -m pip install -e '.[dev]'
python -m pytest code/tests -q
python scripts/check_release.py
python -m build
```

Core tests use synthetic data and scripted clients. They should not require a
model key, live provider or embedding-model download. First-time tokenizer use
can download its encoding table. Avoid altering existing personal memory or
experiment outputs when developing.

`scripts/check_release.py` checks local documentation links, installed public
imports, the CLI, the offline example, live-example dry-run, and a real MCP stdio
initialize/list/status handshake. It does not claim live Writer/Reader validation.

## Changes

- Describe the user-visible problem and the smallest complete change.
- Keep document, citation, transaction and workspace-format behavior compatible.
- For behavior changes, add a focused regression that would fail without the fix.
- Update examples and support boundaries when changing a public interface.
- Report model-backed validation separately from mocked/offline tests.
- Do not change benchmark/Judge semantics as a side effect of framework cleanup.

Use an issue for a bug report or feature proposal. Include Python/OS, commit,
reproduction steps and expected/actual behavior. Redact API keys and private
memory. See [security reporting](SECURITY.md) for sensitive problems.

## Packaging and releases

Build both wheel and source distribution. Test the wheel from outside the source
checkout so imports do not accidentally rely on unshipped files. Check the
source distribution includes README, guides and examples. Validate Python and
platform compatibility before broadening the support statement.

Version `0.1.0` is the current framework baseline; tags and package publication
are separate maintainer actions. Paper results/reproduction remain deferred P1
work. See [release scope](docs/release-scope.md).

## License

This project uses the [MIT License](LICENSE). Preserve copyright and license
notices. Do not introduce third-party code without its required notices and
compatible licensing.
