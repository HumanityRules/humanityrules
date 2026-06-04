# ttyd on webapps

Read this *before* registering a `ttyd` web terminal under `webapps`.

`ttyd` is preinstalled and on `PATH` — do not `brew install` it.

## Recommended command

```bash
mkdir -p /workspace/webapps/projects/term
webapps create term \
    --command 'ttyd -W --port "$WEBAPP_PORT" --interface 127.0.0.1 bash' \
    --cwd /workspace/webapps/projects/term
webapps start term
```

## The one flag trap

### `-W` is mandatory for a writable terminal

Without `-W`, ttyd serves the terminal in **read-only** mode: the page renders, output streams correctly, but keystrokes are silently ignored. The symptom is "the terminal loads but I can't type" — confusing because nothing errors. Always pass `-W` unless you explicitly want a view-only terminal.
