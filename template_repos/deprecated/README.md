# Deprecated template repos

Directories under `template_repos/deprecated/` are former DOH AppTemplate
source trees that are no longer wired into `seed_app_templates.py` and have
no live consumers. They are kept here as historical reference (deployment
shape, Dockerfile, runtime scripts) and are excluded from the active deploy
surface.

Do not add new templates pointing at paths under this directory. If you ever
need to revive one, move it back to `template_repos/<name>/` and re-add a
matching entry to `seed_app_templates.py`.

## Index

- `hermes_docker_agent/` — Docker-in-Docker variant of the Hermes agent
  (used by the former `hermes-docker-personal` and `hermes-docker-slack`
  AppTemplates). Superseded by the nono-sandboxed `hermes_agent/` template
  (`hermes-personal` slug).
- `doh_dind/` — DOH-owned Docker-in-Docker sidecar image (entrypoint +
  snapshotter on top of `docker:26.1.0-dind`). Was the `docker-dind`
  container that paired with `hermes_docker_agent/` to run the agent's
  terminal tools. Unused after that template was retired.
- `seed_app_templates_excerpt.py` — verbatim copy of the helper / template
  definitions removed from `devopshero_app/management/commands/seed_app_templates.py`
  when the docker AppTemplates were retired. Reference only; not imported.
