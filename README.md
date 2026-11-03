# AI Usage for Omarchy

Account-wide AI subscription usage and credit balances in the Omarchy bar.

## Providers

| Provider | Displays | Login source |
| --- | --- | --- |
| Claude | Five-hour and weekly limits, plus model limits when available | Claude Code |
| OpenAI | ChatGPT/Codex session and weekly limits | Codex CLI |
| OpenCode | Go five-hour, weekly and monthly limits; Console budgets when available | OpenCode CLI |
| Antigravity | Gemini, Claude/GPT and Gemini 4 Argon quota pools | Antigravity CLI (`agy`) system keyring login |
| Conductor | AI Junction credit balance | Conductor's saved system keyring login |
| OpenRouter | Credit balance, key limits and spending | API key file, environment or key command |

Subscription limits come from account APIs, including usage on other machines. The plugin does not estimate limits from local message history. Cached readings are marked when a live check fails.

## Install

Requires a current Omarchy shell with plugin support, Python 3, and the relevant CLI logins. Antigravity and Conductor also need `secret-tool` (provided by `libsecret` on Arch) and an unlocked system keyring. OpenCode account limits require a CLI version supporting `opencode api get /api/experimental/usage/limits` (tested with 2.2.5).

```sh
omarchy plugin add https://github.com/whitecliffs/omarchy-aiusage.git --enable
```

Click the lightning icon, open **Setup**, and choose the providers to track. The plugin ID is `jim.aiusage`.

## Display

- **All:** show every tracked provider in the bar.
- **Worst:** show the provider closest to its limit.
- **Cycle:** show one provider every five seconds; pause while the popup is open.
- **Icon only:** show the lightning icon.
- **Show remaining:** switch percentages and meters between used and remaining. Credit balances retain their own units.

The popup stays anchored while open. Provider tabs fit in one row and the popup expands to fit its content, within the available screen height. Warning colors always reflect high usage, even when showing remaining percentages.

Left-click opens the popup; right-click refreshes; middle-click opens Setup. The popup also supports arrow keys, h/j/k/l, number keys to select tabs, R to refresh, and Escape to close.

Widget settings expose the refresh interval, warning percentage, display mode, and OpenRouter options. The default refresh interval is two minutes.

## OpenRouter

Supply a key with `OPENROUTER_API_KEY`, a key command in widget settings, or a private file at `~/.config/omarchy/openrouter/key` (also accepts `~/.config/openrouter/key`). A management key is needed for the account balance; other keys may only expose their own usage.

```sh
install -d -m 700 ~/.config/omarchy/openrouter
install -m 600 /dev/null ~/.config/omarchy/openrouter/key
$EDITOR ~/.config/omarchy/openrouter/key
```

The environment variable must be available to the shell process. A configured key command runs locally as your user.

## Credentials and cache

The plugin reads the tools' existing logins and sends credentials only to the corresponding provider. It does not modify their credential files. Antigravity access-token renewal reads native-client OAuth configuration from the installed `agy` executable and keeps the working configuration and access token in a separate private cache with mode `0600`. `ANTIGRAVITY_CLIENT_ID` and `ANTIGRAVITY_CLIENT_SECRET` can override that configuration. Usage readings are cached under `$XDG_CACHE_HOME/omarchy-aiusage` (normally `~/.cache/omarchy-aiusage`).

Some providers use undocumented endpoints, which may change. If a login expires, open the corresponding CLI to refresh it. Conductor needs a saved AI Junction login; session-only logins are unavailable outside the app.

## Updates

```sh
omarchy plugin update jim.aiusage
```

If a UI change does not appear after updating, run `omarchy restart shell` to clear a stale widget instance.

## Remove

```sh
omarchy plugin remove jim.aiusage
```

This removes the plugin and its bar entry. Your provider logins, OpenRouter key file, and usage cache are kept. The cache is stored at `$XDG_CACHE_HOME/omarchy-aiusage` (normally `~/.cache/omarchy-aiusage`).

## Checks

```sh
omarchy plugin validate .
python3 -m unittest discover -s tests
```

MIT licensed. Independent community plugin; not affiliated with the listed providers.
