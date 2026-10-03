import { currentGame, defineModule, ui, type FormStore, type ModuleCtx, type SettingValue } from "invasor";

// Patito: lsfg-vk frame generation from the panel.
// Game: one switch per game. On = the game gets its own profile (named after it, its id
// in active_in) and all its options show below. Off = the game is taken out of it.
// Manage: lsfg-vk's global options and every profile. Everything lives in lsfg-vk's own
// conf.toml; Patito stores nothing of its own.

interface Status {
  installed: boolean;
  path: string;
  exists: boolean;
  error: string | null;
  /** lsfg-vk's own validator complains about the current conf.toml. */
  lsfg_problem: string | null;
}

interface ProfileInfo {
  name: string;
  games: { entry: string; name: string | null }[];
}

const errorText = (e: unknown) => String((e as Error)?.message ?? e);

/** A FormStore backed by two backend methods. */
function store(ctx: ModuleCtx, get: string, set: string, extra: Record<string, unknown> = {}): FormStore {
  return {
    get: () => ctx.call<Record<string, SettingValue>>(get, extra),
    set: <T extends SettingValue>(key: string, value: T) => ctx.call<T>(set, { ...extra, key, value }),
  };
}

const TESTED = "2.0"; // keep in sync with updates.TESTED

/** A note when the installed lsfg-vk isn't the series Patito was tested with. */
async function compatNote(ctx: ModuleCtx): Promise<HTMLElement[]> {
  try {
    const c = await ctx.call<{ installed: string | null; compat: string | null; tested: string }>("compat");
    if (c.compat === "newer_minor" || c.compat === "unsupported")
      return [ui.info(`⚠ lsfg-vk ${c.installed} is newer than what Patito was tested with (${c.tested}): some options may be missing.`)];
    if (c.compat === "older") return [ui.info(`⚠ lsfg-vk ${c.installed} is older than what Patito was tested with (${c.tested}).`)];
  } catch {
    /* not essential */
  }
  return [];
}

// ---------- Game ----------

let gameEl: HTMLElement | null = null;
let gameSig = "";

async function renderGame(ctx: ModuleCtx, force = false) {
  const el = gameEl;
  if (!el) return;
  const t = currentGame(ctx.game());
  let status: Status | null = null;
  let profiles: ProfileInfo[] = [];
  let current: { entry: string; profile: string | null; custom: boolean } | null = null;
  let problem: string | null = null;
  try {
    status = await ctx.call<Status>("status");
    if (!status.error) {
      profiles = await ctx.call<ProfileInfo[]>("profiles");
      if (t) current = await ctx.call("game_profile", { appid: t.game.appid, shortcut: t.game.shortcut });
    }
  } catch (e) {
    problem = errorText(e);
  }
  const sig = JSON.stringify([t?.game.appid, t?.how, status, profiles.map((p) => p.name), current, problem]);
  if (!force && sig === gameSig) return; // nothing changed: keep the ring where it is
  gameSig = sig;

  const parts: HTMLElement[] = await compatNote(ctx);
  if (status && !status.installed) parts.push(ui.info("lsfg-vk doesn't seem to be installed (no Vulkan layer found)."));
  if (status?.error) parts.push(ui.info(status.error));
  if (!t) {
    parts.push(ui.info("Open or highlight a game in the Library."));
  } else {
    parts.push(ui.info(`${t.game.name ?? `App ${t.game.appid}`} (${t.how})`));
    if (problem) parts.push(ui.info(problem));
    else if (status?.error) {
      /* explained above */
    } else if (current) {
      const game = t.game;
      const cur = current;
      const args = { appid: game.appid, shortcut: game.shortcut };
      parts.push(
        ui.toggle({
          label: "Frame generation",
          value: cur.profile !== null,
          hint: "Turns lsfg-vk on for this game with its own profile. Applies the next time the game starts.",
          navHints: false,
          onChange: async (on) => {
            try {
              if (on) {
                const made = await ctx.call<{ profile: string }>("make_custom", { ...args, title: game.name });
                ctx.toast(`On: profile “${made.profile}”`);
              } else {
                await ctx.call("set_game_profile", { ...args, name: null });
                ctx.toast("Off for this game");
              }
            } catch (e) {
              ctx.toast(`Couldn't change it: ${errorText(e)}`, "error");
            }
            void renderGame(ctx, true); // show or hide the options
          },
        }),
      );
      if (cur.profile && cur.custom) {
        parts.push(await ui.form(ctx, "profile", store(ctx, "profile_get", "profile_set", { name: cur.profile }), { navHints: false }));
      } else if (cur.profile) {
        // Set up elsewhere (e.g. lsfg-vk-ui) with a profile other games use too.
        const n = profiles.find((p) => p.name === cur.profile)?.games.length ?? 0;
        parts.push(
          ui.info(`Uses the shared profile “${cur.profile}”${n > 1 ? ` (${n} games)` : ""}.`),
          ui.button({
            label: "Give this game its own profile",
            onClick: async () => {
              try {
                const made = await ctx.call<{ profile: string }>("make_custom", { ...args, title: game.name });
                ctx.toast(`Profile “${made.profile}” for this game`);
              } catch (e) {
                ctx.toast(errorText(e), "error");
              }
              void renderGame(ctx, true);
            },
          }),
        );
      }
      parts.push(ui.info(`Matched by its id: ${cur.entry}${game.shortcut ? " (non-Steam)" : ""}`));
      if (cur.profile) parts.push(ui.info("Changes apply the next time the game starts."));
    }
  }
  el.replaceChildren(...parts);
}

// ---------- Profiles ----------

let profilesEl: HTMLElement | null = null;
let selected: string | null = null;

async function renderProfiles(ctx: ModuleCtx, pick?: string) {
  const el = profilesEl;
  if (!el) return;
  let list: ProfileInfo[];
  try {
    list = await ctx.call<ProfileInfo[]>("profiles");
  } catch (e) {
    el.replaceChildren(ui.info(errorText(e)));
    return;
  }
  const names = list.map((p) => p.name);
  selected = pick !== undefined && names.includes(pick) ? pick : selected && names.includes(selected) ? selected : (names[0] ?? null);

  const run = (label: string, fn: () => Promise<void>) =>
    ui.button({
      label,
      onClick: async () => {
        try {
          await fn();
        } catch (e) {
          ctx.toast(errorText(e), "error");
        }
      },
    });
  const newName = ui.text({ label: "Name", value: "", maxLength: 64, placeholder: "for create / duplicate / rename" });
  const typed = () => newName.get().trim();
  const manage: HTMLElement[] = [
    newName,
    run("Create", async () => {
      await ctx.call("profile_create", { name: typed() });
      ctx.toast(`Profile “${typed()}” created`);
      await renderProfiles(ctx, typed());
    }),
  ];

  const parts: HTMLElement[] = [];
  if (selected) {
    const name = selected;
    const profile = list.find((p) => p.name === name)!;
    parts.push(
      ui.select({ label: "Profile", value: name, options: names.map((n) => ({ value: n, label: n })), onChange: (v) => void renderProfiles(ctx, v) }),
      await ui.form(ctx, "profile", store(ctx, "profile_get", "profile_set", { name }), { navHints: false }),
      ui.section(
        "Games using it",
        profile.games.length
          ? profile.games.map((g) => ui.info(g.name ? `${g.name} (${g.entry})` : g.entry))
          : [ui.info("No games use it.")],
        { open: false },
      ),
    );
    manage.push(
      run("Duplicate this profile", async () => {
        await ctx.call("profile_create", { name: typed(), copy_from: name });
        ctx.toast(`“${name}” duplicated as “${typed()}”`);
        await renderProfiles(ctx, typed());
      }),
      run("Rename this profile", async () => {
        await ctx.call("profile_rename", { old: name, new: typed() });
        ctx.toast(`Renamed to “${typed()}”`);
        await renderProfiles(ctx, typed());
      }),
      run("Delete this profile", async () => {
        const games = profile.games.length ? ` Its ${profile.games.length} game(s) will go back to no frame generation.` : "";
        if (!(await ui.confirm(`Delete the profile “${name}”?${games}`, { ok: "Delete" }))) return;
        await ctx.call("profile_delete", { name });
        ctx.toast(`Profile “${name}” deleted`);
        await renderProfiles(ctx);
      }),
    );
  } else {
    parts.push(ui.info("No profiles yet: type a name below and create one."));
  }
  parts.push(ui.section("Create, duplicate, rename, delete", manage, { open: !selected }));
  el.replaceChildren(...parts);
  gameSig = ""; // the Game tab's list of profiles may have changed
}

// ---------- module ----------

let skipShow = false; // a tab's first onShow comes right after its render

export default defineModule({
  tabs: [
    {
      label: "Game",
      async render(el, ctx) {
        gameEl = el;
        await renderGame(ctx, true);
      },
      onShow: (ctx) => void renderGame(ctx),
    },
    {
      label: "Manage",
      async render(el, ctx) {
        const globalEl = document.createElement("div");
        profilesEl = document.createElement("div");
        el.append(...(await compatNote(ctx)), profilesEl, globalEl);
        skipShow = true;
        await renderProfiles(ctx);
        try {
          const status = await ctx.call<Status>("status");
          const body: HTMLElement[] = [ui.info(`Config: ${status.path}`)];
          if (status.lsfg_problem) el.prepend(ui.info(`⚠ lsfg-vk reports a problem in conf.toml: ${status.lsfg_problem}`));
          if (status.error) body.push(ui.info(status.error));
          else body.push(await ui.form(ctx, "global", store(ctx, "global_get", "global_set"), { navHints: false }));
          globalEl.append(ui.section("Global options", body, { open: false }));
        } catch (e) {
          globalEl.append(ui.info(errorText(e)));
        }
      },
      // Another tool (lsfg-vk-ui) may have changed the file meanwhile.
      onShow: (ctx) => {
        if (skipShow) skipShow = false;
        else void renderProfiles(ctx);
      },
    },
    {
      label: "Updates",
      render(el, ctx) {
        const result = document.createElement("div");
        const check = async () => {
          result.replaceChildren(ui.info("Checking builds.lsfg-vk.dev…"));
          let st: {
            installed: string | null;
            latest: string;
            state: string;
            path: string | null;
            tested: string;
            latest_compat: string | null;
          };
          try {
            st = await ctx.call("update_status");
          } catch (e) {
            result.replaceChildren(ui.info(`Couldn't check: ${errorText(e)}`));
            return;
          }
          const lines: HTMLElement[] = [
            ui.info(`Installed: ${st.installed ?? (st.state === "not_installed" ? "not installed" : "unknown version")}`),
            ui.info(`Latest stable: ${st.latest}`),
          ];
          if (st.state === "up_to_date") lines.push(ui.info("You're up to date."));
          else if (st.state === "ahead") lines.push(ui.info(`Your version is newer than the latest stable (${st.latest}).`));
          else if (st.state === "system")
            lines.push(ui.info(`lsfg-vk is installed by your system (${st.path}), not in ~/.local: update it with your package manager.`));
          else if (st.latest_compat === "unsupported")
            lines.push(ui.info(`lsfg-vk ${st.latest} is a new major version that this Patito doesn't know: update Patito first.`));
          else
            lines.push(
              ui.button({
                label: `Install ${st.latest}`,
                onClick: async () => {
                  let what = st.installed ? `Update lsfg-vk from ${st.installed} to ${st.latest}?` : `Install lsfg-vk ${st.latest} in ~/.local?`;
                  if (st.latest_compat === "newer_minor")
                    what += ` Patito was tested with lsfg-vk ${st.tested}: new options won't show here (set them with lsfg-vk-ui).`;
                  if (!(await ui.confirm(what, { ok: "Install" }))) return;
                  ctx.toast(`Installing lsfg-vk ${st.latest}…`);
                  try {
                    await ctx.call("update_install");
                    ctx.toast(`lsfg-vk ${st.latest} installed`);
                  } catch (e) {
                    ctx.toast(`Not installed: ${errorText(e)}`, "error");
                  }
                  void check();
                },
              }),
              ui.info("Games use the new version the next time they start."),
            );
          result.replaceChildren(...lines);
        };
        el.append(
          ui.info(`This Patito is tested with lsfg-vk ${TESTED}.`),
          ui.info("Stable releases of lsfg-vk from builds.lsfg-vk.dev, installed into ~/.local."),
          ui.button({ label: "Check for updates", onClick: () => void check() }),
          result,
        );
      },
    },
    {
      label: "Credits",
      render(el) {
        el.append(
          ui.info("Patito is a panel for lsfg-vk, Lossless Scaling frame generation on Linux (lsfg-vk.dev)."),
          ui.separator(),
          ui.info("Thanks to PancakeTAS and every lsfg-vk contributor for bringing frame generation to Linux."),
          ui.info("Thanks to THS, the developer of Lossless Scaling, whose frame generation lsfg-vk uses (you need your own copy of Lossless Scaling)."),
          ui.separator(),
          ui.info("Patito is not affiliated with lsfg-vk or Lossless Scaling. Please don't report problems with this module to them."),
        );
      },
    },
  ],
  tabsAlign: "justify",
  onGameChange: (_game, ctx) => void renderGame(ctx),
  destroy: () => {
    gameEl = profilesEl = null;
    gameSig = "";
    selected = null;
  },
});
