# Advanced Workflows

## Extraction with dependency resolution

Extract files matching a natural language query, pulling in dependencies:

```bash
kapa-cortex --extract "authentication changes"
```

This finds files matching "authentication", then walks the dependency graph
to include files they depend on. Use `--no-deps` to skip this.

## Partial plan execution

If a step fails mid-plan:

```bash
kapa-cortex --check-plan           # see which steps are done/failed
kapa-cortex --run-plan --step 5    # retry the failed step
kapa-cortex --run-plan             # continue remaining steps
```

## Custom base branch

```bash
kapa-cortex --base develop         # diff against develop
kapa-cortex --base release/2.0     # diff against release branch
```

## Large branches

Adjust PR sizing for branches with many changes:

```bash
kapa-cortex --max-files 5 --max-lines 400   # larger PRs
kapa-cortex --max-files 2 --max-lines 100   # smaller PRs
```

## Visualization

```bash
kapa-cortex --visualize              # DOT to stdout
kapa-cortex --dot-file deps.dot      # DOT to file
dot -Tpng deps.dot -o deps.png       # render with graphviz
```

## CI integration

Generate a shell script for automated execution:

```bash
kapa-cortex --shell-script > stack.sh
chmod +x stack.sh
./stack.sh
```

## Daemon for repeated analysis

Start once, query many times:

```bash
kapa-cortex --daemon &               # background
kapa-cortex --query "analyze"        # instant analysis
kapa-cortex --query "status"         # check daemon health
kapa-cortex --daemon-stop            # shutdown
```

## Troubleshooting

**"No changes found"** — verify you're on a feature branch ahead of base:
```bash
kapa-cortex --show-base              # what base was detected?
git log --oneline $(kapa-cortex --show-base)..HEAD
```

**Plan step failed** — check the error, fix the issue, retry:
```bash
kapa-cortex --check-plan             # shows error message
kapa-cortex --run-plan --step N      # retry specific step
```

**Stale caches** — rebuild:
```bash
kapa-cortex --index
```
