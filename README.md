# Ducky

A module for [Invasor](../invasor) that sets up **lsfg-vk** frame generation per game, from Steam's
gamepad UI.

- **Game:** one switch for the game you're on. When it's on, the game gets its own lsfg-vk profile, named after
  it with its id in `active_in`, and all its options show below it.
- **Manage:** every profile (edit, create, duplicate, rename, delete) and lsfg-vk's global options.
- **Updates:** checks builds.lsfg-vk.dev for the latest stable lsfg-vk and installs it into `~/.local`. The installed
  version is read from lsfg-vk's own library, so it's right however lsfg-vk was installed. A newer (git) build or a
  system install is never replaced.

lsfg-vk's own `~/.config/lsfg-vk/conf.toml` is the only place where settings are kept. Ducky reads and writes
it without losing anything, and keeps a backup (`conf.toml.invasor-backup`) the first time it changes it. Every change
is checked by lsfg-vk itself (`lsfg-vk-cli validate`) before it replaces the file, when lsfg-vk-cli is available.

## Requirements
- Invasor (module API 1) with `author` support in module.json.
- [lsfg-vk](https://lsfg-vk.dev) 2.x installed (tested with 2.0.x), and your own copy of Lossless Scaling. Newer 2.x
  releases work, though options they add may not show in Ducky; a new major version needs a newer Ducky.

## Build, test and install
With the core checked out next to this repository (`../invasor`):

```sh
python3 ../invasor/tools/pack_module.py ducky       # build, check, tests -> ducky-<version>.zip
python3 ../invasor/tools/install_module.py ducky    # or install that zip from ⚙ Settings › Install module
```

Tests on their own: `INVASOR_CORE=../invasor python3 -m unittest discover -s ducky`.

## Credits
lsfg-vk is by PancakeTAS and its contributors; Lossless Scaling and its frame generation belong to their
developers. Ducky is not affiliated with either project.
Please don't report problems with this module to them.

## License
[GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later).
