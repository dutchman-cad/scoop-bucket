# Dutchman Scoop bucket

Install [Scoop](https://scoop.sh), then add this bucket:

```powershell
scoop bucket add dutchman-cad https://github.com/dutchman-cad/scoop-bucket
```

Install Dutchman Convert for the `dutchman` command, `dutchman-mcp`, and the
Dutchman Convert desktop app shortcut:

```powershell
scoop install dutchman-cad/dutchman
```

Install Pilot for `dutchman-pilot` and `dutchman-pilot-eval`:

```powershell
scoop install dutchman-cad/dutchman-pilot
```

Update Scoop and installed apps:

```powershell
scoop update
scoop update dutchman dutchman-pilot
```

Manifests follow each release automatically within about an hour, after Windows
installation checks pass. Dutchman Convert follows stable releases. Pilot follows
the newest release, including prereleases, until a stable release exists, then
follows stable releases. Releases tagged `nightly` are excluded.

Licensed under [Apache-2.0](LICENSE).
