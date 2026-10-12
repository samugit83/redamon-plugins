## What this changes

<!-- The plugin and what it does, or what this change fixes or adds. Link the issue if there is one. -->

## Checklist

- [ ] I read [CONTRIBUTING.md](https://github.com/samugit83/redamon-plugins/blob/master/CONTRIBUTING.md); I understand an administrator reviews, merges and releases this, and that merging is not a release.
- [ ] `tools/run.sh validate <plugin>` and `tools/run_tests.sh <plugin>` pass.
- [ ] The plugin asks for the fewest permissions that work, and its README says why it needs each one, what it reads, what it stores and what a person must approve.
- [ ] `version` in `plugin.json` is raised (or is `1.0.0` for a new plugin) and `CHANGELOG.md` has its section.
- [ ] No real target data anywhere: no real hostnames, IP addresses, scan output, captured traffic or credentials in code, tests, fixtures or eval files.
- [ ] Nothing in `vendor/` is compiled or minified, and every bundled library keeps its licence file.
- [ ] A new plugin only: `/<id>/ @samugit83` is in `.github/CODEOWNERS` and the plugin has its block under "The plugins" in `README.md`.
- [ ] I did not edit `catalog.json`, `catalog.json.sig`, `tools/`, `.github/workflows/`, `.platform-sdk/` or another plugin's folder.
