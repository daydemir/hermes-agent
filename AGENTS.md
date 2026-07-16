# Hermes Agent — agent development rules

This file is the compact, always-loaded contract for coding agents working on
Hermes. Read `CONTRIBUTING.md` for setup, architecture, skill authoring,
cross-platform examples, security background, and the PR process. Read the
nearest nested `AGENTS.md` before editing a scoped app such as
`apps/desktop/`.

Never give up on the right solution.

## Product invariants

Hermes runs one agent core across CLI, messaging gateways, TUI, desktop, and
scheduled work. It learns across sessions and delegates to subagents.

- **Prompt caching is sacred.** Keep the system prompt and tool schemas
  byte-stable for a conversation. Never mutate past context, swap toolsets, or
  reload memories mid-session. Context compression is the sole exception.
- **The core is a narrow waist.** Capabilities belong at the edges. Prefer, in
  order: extend existing code; CLI + skill; service-gated tool; plugin; MCP;
  new core model tool. Every core tool schema is paid for on every model call.
- **Preserve strict role alternation.** Never inject two adjacent messages of
  the same role or a synthetic user message mid-loop.
- **Verify the premise before fixing it.** Reproduce on current `main`, trace
  the real runtime path, and read original intent with `git log -p -S` when an
  omission or restriction may be deliberate. Fix the whole bug class, not one
  reported call site.
- **Preserve contributor credit.** Build on external work when possible so
  authorship survives.

Hermes grows aggressively at the edges—platforms, providers, desktop/TUI,
skills—and conservatively in the core agent/tool schema. These goals do not
conflict.

## Contribution decisions

Wanted:

- Real bug fixes with a current reproduction and behavioral tests.
- Cross-platform, security, performance, and robustness improvements.
- New platforms, providers, interfaces, and broadly useful skills integrated
  through existing setup and configuration UX.
- Focused extraction of god-files into coherent modules.
- Behavior and invariant tests, plus real-path E2E checks for config,
  resolution, security, filesystem, network, and remote-backend work.

Do not add:

- Speculative hooks, callbacks, managers, or extension points without a real
  consumer.
- Non-secret settings in `.env`; put behavior in `config.yaml`.
- A core tool when terminal, file, a skill, plugin, or MCP covers the need.
- Pagination on instructional content agents must read fully.
- Security fixes that destroy the feature being secured.
- Outbound telemetry or attribution without explicit user opt-in.
- Dead wiring without real-path E2E proof.
- Plugins that modify core files or third-party product integrations inside
  this repository. Ship third-party products as standalone plugins installed
  under `~/.hermes/plugins/` or via a package entry point.
- New in-tree memory providers; the bundled set is closed. Standalone memory
  plugins implement `MemoryProvider` and use the existing discovery path.

Automated triage may close only for `implemented_on_main`,
`cannot_reproduce`, or `incoherent`. Taste or scope rejection belongs to a
human maintainer; when uncertain, leave the contribution open.

## Find the real implementation

The filesystem and source are canonical; counts and examples drift.

| Area | Load-bearing entry points |
|---|---|
| Agent loop | `run_agent.py`, `agent/` |
| Tool discovery/dispatch | `model_tools.py`, `tools/registry.py`, `tools/*.py` |
| Tool exposure | `toolsets.py` |
| CLI | `cli.py`, `hermes_cli/` |
| Sessions | `hermes_state.py` |
| Gateway | `gateway/run.py`, `gateway/session.py`, `gateway/platforms/` |
| TUI | `ui-tui/`, `tui_gateway/` |
| Desktop | `apps/desktop/` and its own `AGENTS.md` |
| Plugins | `plugins/`, `hermes_cli/plugins.py` |
| Cron | `cron/jobs.py`, `cron/scheduler.py` |
| Tests | `tests/`, `scripts/run_tests.sh` |

Dependency direction is:

```text
tools/registry.py <- tools/*.py <- model_tools.py <- run_agent.py / cli.py
```

Do not introduce reverse imports across this chain.

## Implementation style

- Keep changes focused. Extend existing infrastructure before adding surface.
- Use PEP 8 pragmatically. Comment intent, trade-offs, and API quirks; do not
  narrate obvious code.
- Catch specific exceptions and log unexpected failures with `exc_info=True`.
- Preserve prompt caching and message alternation in every agent-loop change.
- Use config and setup UX, not raw environment variables, for behavior.
- Assume Linux, macOS, native Windows, and WSL can reach shared code.

### TypeScript

- Shared or distant UI state belongs in small, feature-owned nanostores;
  rendering components use `useStore`, actions use `$atom.get()`.
- Keep route roots thin and hooks single-purpose. Prefer colocated actions to
  god hooks and direct leaf subscriptions to prop drilling.
- Use `void` explicitly for fire-and-forget handlers.
- Prefer interfaces for public props and shared object shapes; extend React
  primitive props with `ComponentProps`, `Pick`, or `Omit`.
- Prefer tables over condition ladders for id/route/view mappings.

## Tools and toolsets

Before adding a tool, apply the footprint ladder in Product invariants. Local
or niche tools belong in a plugin via `ctx.register_tool(...)`.

A built-in tool requires exactly:

1. A `tools/<name>.py` module that registers schema, handler, availability,
   and requirements with `tools.registry.registry`.
2. Its canonical name in the appropriate `toolsets.py` toolset.

Auto-discovery imports registered modules; it does not expose them. Handlers
must return strings, usually JSON. Tool-schema path text uses
`display_hermes_home()`. Persistent state uses `get_hermes_home()`, never a
hardcoded `~/.hermes` path. Agent-level tools such as memory and todo are
intercepted by `run_agent.py`; follow an existing sibling.

Schema descriptions must not name tools from other toolsets because those
tools may be unavailable. Add availability-aware cross-references dynamically
in `model_tools.py` only when essential.

## Commands and interfaces

Slash commands are canonicalized in `hermes_cli/commands.py` through
`COMMAND_REGISTRY`. Add the `CommandDef` there, then add only the surface
handlers that genuinely support it. Aliases belong solely on the existing
`CommandDef`; help, Telegram, Slack, autocomplete, and registries derive from
it.

The dashboard chat embeds the real `hermes --tui` through a PTY. Do not rebuild
its transcript or composer in React; extend Ink. Supporting React panels are
fine when independent of the PTY session.

Desktop is a separate React/nanostore client over `tui_gateway` JSON-RPC.
Follow `apps/desktop/AGENTS.md`. Its slash palette may hide irrelevant built-in
commands, but must continue to admit skill and quick-command extensions through
`isDesktopSlashExtensionCommand` in both catalog and suggestion paths.

## Configuration and profiles

Secrets only go in `.env`. Add secret metadata to `OPTIONAL_ENV_VARS` in
`hermes_cli/config.py`. All timeouts, thresholds, flags, paths, and display
preferences go in `config.yaml` and `DEFAULT_CONFIG`.

Adding a key to an existing config section does not require a version bump.
Bump `_config_version` only for a migration or structural transformation.

Know all three loaders:

| Loader | Consumers |
|---|---|
| `cli.py::load_cli_config()` | interactive CLI |
| `hermes_cli/config.py::load_config()` | setup and most CLI subcommands |
| direct YAML in `gateway/run.py` / `gateway/config.py` | gateway runtime |

Profile support is mandatory:

- `_apply_profile_override()` sets `HERMES_HOME` before module imports.
- Use `get_hermes_home()` for state and `display_hermes_home()` for messages.
- Module constants may cache those helpers after profile initialization.
- Tests that mock `Path.home()` must also set `HERMES_HOME`.
- Profile enumeration remains anchored at `Path.home()/.hermes/profiles`, not
  the active `HERMES_HOME`; this is intentional.
- Credentialed platform adapters acquire and release a scoped token lock.

CLI uses its process cwd. Messaging uses `terminal.cwd` from `config.yaml`;
the gateway may bridge it internally to `TERMINAL_CWD`. Do not restore the
removed `MESSAGING_CWD` setting or document `TERMINAL_CWD` as user config.

## Plugins and providers

General plugins register through `hermes_cli/plugins.py` and may add lifecycle
hooks, tools, or CLI subcommands. Discovery normally occurs when
`model_tools.py` imports; a path that reads plugin state before that must call
the idempotent discovery function explicitly.

Memory providers use the separate `MemoryProvider` / `memory_manager` system.
Only the active provider exposes its CLI. New providers ship outside this
repository.

Model-provider plugins use another lazy discovery path in `providers`:

1. bundled `plugins/model-providers/`
2. user `$HERMES_HOME/plugins/model-providers/`
3. legacy `providers/`

User registrations override bundled registrations by name. The general plugin
manager records model-provider manifests but must not import them a second
time. See `website/docs/developer-guide/model-provider-plugin.md`.

Plugins must never special-case themselves in `run_agent.py`, `cli.py`,
`gateway/run.py`, or `hermes_cli/main.py`. If a real consumer needs more
capability, widen the generic plugin contract.

## Skills

Built-in skills live under `skills/`; heavier official skills live under
`optional-skills/`. Read `CONTRIBUTING.md` and the `hermes-agent-dev` skill's
`references/new-skill-pr-salvage.md` before adding or salvaging one.

Hard requirements:

- Frontmatter description is one sentence, at most 60 characters, without
  marketing language.
- Credit the human contributor first.
- Use `platforms:` when scripts rely on OS-specific behavior; prefer portable
  stdlib paths and primitives where practical.
- Prose names native Hermes tools or an explicitly required MCP; third-party
  CLIs belong behind `terminal` and helper scripts.
- Body order: When to Use, Prerequisites, How to Run, Quick Reference,
  Procedure, Pitfalls, Verification.
- Put executable helpers in `scripts/`, long reference material in
  `references/`, and reusable assets in `templates/`.
- Test with stdlib + pytest + mocks only, no live network, under
  `scripts/run_tests.sh tests/skills/test_<skill>_skill.py -q`.
- Limit `.env.example` edits to the skill's clearly delimited block.

## Delegation and durable work

`delegate_task` creates isolated child context. Leaf agents cannot delegate or
use privileged conversational tools. Orchestrators can delegate only when
enabled and within configured depth/concurrency. Background delegation returns
immediately but is process-local; use cron or a background terminal process
with completion notification when work must survive a restart.

Curator may manage only agent-created skills, never bundled or hub-installed
skills. It never deletes; its maximum destructive action is archive. Pinned
skills are exempt from automatic transitions.

Cron jobs live in `cron/` and support duration, natural recurring phrases,
five-field cron, and ISO one-shot schedules. Preserve these invariants:

- hard interrupt for runaway cron sessions;
- tick lock prevents duplicate schedulers;
- cron skips memory by default;
- delivery remains in the cron session rather than mutating a gateway
  conversation's role history.

Kanban is a durable SQLite work queue. The board is the hard isolation boundary
and workers must retain `HERMES_KANBAN_BOARD`. Tenant is only a namespace
within a board. The dispatcher reclaims stale claims and blocks repeated
failures; do not bypass its atomic claim path.

## Gateway invariants

- Background-process notifications are configuration-driven. Do not hardcode
  verbosity.
- The gateway has two sequential guards while an agent runs: the base adapter
  pending-message guard and the runner's control-command interception. Any new
  approval or control command that must reach a blocked agent must bypass both
  and dispatch inline.
- Gateway platform adapters with unique credentials use scoped locks so two
  profiles cannot connect with the same token.
- Slash commands that mutate system-prompt state defer invalidation to the next
  session by default; an explicit `--now` may invalidate immediately.

## Cross-platform and security

Read the complete checklist in `CONTRIBUTING.md` and run
`scripts/check-windows-footguns.py` for OS-sensitive changes.

- Never use `os.kill(pid, 0)`; use `psutil.pid_exists` or the existing wrapper.
- Check external executables with `shutil.which`; invoke Python with
  `sys.executable`, not a shebang assumption.
- Guard Unix-only imports, syscalls, signals, process groups, file modes, and
  symlink tests. Use `psutil` for cross-platform process trees.
- Use `pathlib.Path`, explicit encodings, and Windows-aware subprocess/path
  behavior. Keep `install.sh` and `install.ps1` equivalent.
- Quote user-controlled shell values with `shlex.quote`, resolve symlinks
  before path authorization, and never log secrets.
- Preserve dangerous-command approvals, protected-path checks, cron injection
  scanning, skill guards, sandbox secret stripping, and container hardening.

Dependencies require bounded supply-chain pins:

- PyPI: `>=floor,<next-major` (tight minor ceiling for pre-1.0 packages).
- Git URLs and GitHub Actions: full commit SHA; action pins keep a version
  comment.
- CI-only installs: exact versions.
- Regenerate `uv.lock` after Python dependency changes.

## Known load-bearing pitfalls

- Do not add `simple_term_menu`; new menus use `hermes_cli/curses_ui.py`.
- Do not emit `\033[K` through prompt-toolkit; clear spinner lines with space
  padding.
- `model_tools.py::_last_resolved_tool_names` is process-global and is saved
  and restored around subagent execution.
- Do not squash-merge a stale branch; update it first and inspect the resulting
  diff for unrelated reversions.
- Never wire unused modules into live paths without real E2E validation.
- Tests must never write to the user's real Hermes home.

## Testing and completion

Always run tests through `scripts/run_tests.sh`; never call pytest directly for
final verification. The wrapper supplies isolated subprocesses, credential
scrubbing, temporary `HERMES_HOME`, UTC, stable locale, and CI-parity workers.
Run the narrowest relevant test during iteration, then the appropriate broader
gate before handoff.

Tests assert behavior or relationships, not mutable snapshots such as model
names, config version literals, or enumeration counts. Never test by reading
source text or regex-matching implementation shape; extract pure logic and
execute it. OS-specific tests require explicit platform markers.

Before handoff:

1. Exercise the real changed path, not only mocked units.
2. Run `scripts/run_tests.sh <relevant target>`.
3. Run Windows-footgun checks for OS/process/filesystem changes.
4. Confirm no secret, real-home state, cache invalidation, or role-alternation
   regression was introduced.
5. Keep the worktree focused and clean.
