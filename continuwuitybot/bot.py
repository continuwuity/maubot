import asyncio
import json
import time
import warnings
from typing import Type
from urllib.parse import quote

import aiohttp
from maubot import MessageEvent, Plugin
from maubot.handlers import command, event
from mautrix.types import EventType, ReactionEvent
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

try:
    from resolvematrix.cache import VoidResolutionCache
    from resolvematrix.async_ import AsyncClientResolver, AsyncServerResolver
except ImportError:
    warnings.warn("resolvematrix is not installed in this environment; disabling commands")
    AsyncClientResolver = AsyncServerResolver = VoidResolutionCache = None

from .vendor.color_contrast import AccessibilityLevel, ModulationMode
from .vendor.color_contrast import modulate as modulate_colour

WASTEBASKET = "\N{WASTEBASKET}\N{VARIATION SELECTOR-16}"
WARNING_SIGN = "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}"
CHECKMARK = "\N{WHITE HEAVY CHECK MARK}"
CROSS = "\N{CROSS MARK}"
S2S_STEPS = {
    1.0: "IP literal with explicit port",
    2.0: "domain name with explicit port",
    3.1: "well-known delegation to IP literal",
    3.2: "well-known delegation to domain name with explicit port",
    3.3: "well-known delegation to domain name using `_matrix-fed._tcp` SRV record",
    3.4: "well-known delegation to domain name using deprecated `_matrix._tcp` SRV record",
    3.5: "well-known delegation to domain name using default port",
    4.0: "`_matrix-fed._tcp` SRV record",
    5.0: "deprecated `_matrix._tcp` SRV record",
    6.0: "default port"
}


def colour_span(text: str, *, fg: str | None = None, bg: str | None = None) -> str:
    span = "<span"
    if fg:
        span += f' data-mx-color="{fg}"'
    if bg:
        span += f' data-mx-bg-color="{bg}"'
    span += f">{text}</span>"
    return span


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("repo")
        helper.copy("repo.owner")
        helper.copy("repo.name")
        helper.copy("forge")


class ContinuwuityHelper(Plugin):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_sent: dict[str, float] = {}
        self.getter_lock = asyncio.Lock()
        self.server_resolver = AsyncServerResolver(cache=VoidResolutionCache()) if AsyncServerResolver else None
        self.client_resolver = AsyncClientResolver(cache=VoidResolutionCache()) if AsyncClientResolver else None

    async def start(self) -> None:
        self.config.load_and_update()

    async def stop(self) -> None:
        pass

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    @property
    def main_org(self) -> str:
        return self.config.get("repo", {}).get("owner", "continuwuation")

    @property
    def main_repo(self) -> str:
        return self.config.get("repo", {}).get("name", "continuwuity")

    @property
    def forge(self) -> str:
        return self.config.get("forge", "https://forgejo.ellis.link").rstrip("/")

    @property
    def base_url(self):
        return self.forge.rstrip("/") + "/api/v1"

    async def get_issue(
        self, n: int, repo: str = "continuwuation/continuwuity", *, base_url: str | None = None
    ) -> dict | None | Exception:
        url = (base_url or self.base_url) + "/repos/%s/issues/%d" % (repo, n)
        try:
            self.log.info("-> GET %s", url)
            async with self.http.get(url) as resp:
                self.log.info("<- GET %s [%d %s]", url, resp.status, resp.reason)
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return None
            return e

    async def get_pull_request(
        self, n: int, repo: str = "continuwuation/continuwuity", *, base_url: str | None = None
    ) -> dict | None | Exception:
        """
        Get a pull request by number.

        :param n: The pull request number
        :param repo: The repository to pull from. Defaults to "continuwuation/continuwuity"
        :return: The data, None if not found
        """
        url = (base_url or self.base_url) + "/repos/%s/pulls/%d" % (repo, n)
        try:
            self.log.info("-> GET %s", url)
            async with self.http.get(base_url or self.base_url + "/repos/%s/pulls/%d" % (repo, n)) as resp:
                self.log.info("<- GET %s [%d %s]", url, resp.status, resp.reason)
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return None
            return e

    @event.on(EventType.REACTION)
    async def on_reaction(self, evt: ReactionEvent):
        if evt.sender == self.client.mxid:
            return
        if evt.content.relates_to.key != WASTEBASKET:
            self.log.debug("ignoring reaction with key %s", evt.content.relates_to.key)
            return
        target_event = await self.client.get_event(evt.room_id, evt.content.relates_to.event_id)
        if not isinstance(target_event, MessageEvent):
            self.log.debug("ignoring reaction to non-message event")
            return
        if target_event.sender != self.client.mxid:
            self.log.debug("ignoring reaction to event sent by someone else (%s)", target_event.sender)
            return
        self.log.debug(
            "cleaning up event %s in room %s as requested by %s", target_event.event_id, evt.room_id, evt.sender
        )
        await self.client.redact(evt.room_id, target_event.event_id, reason=f"Deletion requested by {evt.sender}")

    @command.passive(r"([a-zA-Z]+/)?([a-zA-Z]+)?[#!](\d+)", multiple=True)
    async def on_issue_number(self, evt: MessageEvent, matches: list[tuple[str]]):
        if evt.content.relates_to.rel_type == "m.replace":
            return  # don't react to message edits
        now = time.time()
        await self.client.set_fully_read_marker(evt.room_id, evt.event_id, evt.event_id)
        t: list[asyncio.Task] = []
        cache_set = set()
        async with self.getter_lock:
            async with asyncio.TaskGroup() as tg:
                to_get = set()
                for match_set in matches:
                    m = list(match_set)
                    m.pop(0)
                    org = m.pop(0) or self.main_org
                    # Trim trailing slash if present
                    if org.endswith("/"):
                        org = org[:-1]
                    repo = m.pop(0) or self.main_repo
                    n = int(m.pop(0))
                    if n in to_get or n < 100:
                        continue
                    to_get.add(n)
                    key = f"{evt.room_id};{org};{repo};{n}"
                    cache_set.add(key)

                    last_sent = self.last_sent.get(key, 0)
                    if now - last_sent < 60:
                        await self.client.react(evt.room_id, evt.event_id, "⏳")
                        self.log.info(
                            "Ignoring request for %s/%s#%d as it was sent %.1fs ago", org, repo, n, now - last_sent
                        )
                        continue
                    self.log.info("Fetching issue %d from %s/%s", n, org, repo)
                    t.append(tg.create_task(self.get_issue(n, f"{org}/{repo}"), name=key))

        lines = []
        for task in t:
            result = task.result()
            if result is None:
                cache_set.remove(task.get_name())
                continue
            elif isinstance(result, Exception):
                cache_set.remove(task.get_name())
                self.log.error("Error while fetching %s: %s", task.get_name(), result, exc_info=result)
                continue
            line = (
                "* [#{0[number]} ({0[state]}): {0[title]}]({0[html_url]}) by "
                "[{0[user][username]}]({0[user][html_url]})".format(result).replace("<", "&lt;").replace(">", "&gt;")
            )
            labels = []
            for label in result["labels"]:
                url = self.forge + "/{}s?q=&labels={!s}".format(
                    "pull" if "pull_request" in result else "issue", label["id"]
                )
                fg, bg, ok = modulate_colour(
                    "#" + label["color"], "#171E26", level=AccessibilityLevel.AAA, mode=ModulationMode.FOREGROUND
                )
                if ok:
                    self.log.debug("modulated #%s to %s on %s with success.", label["color"], fg.hex, bg.hex)
                    fg = fg.hex
                    bg = bg.hex
                else:
                    self.log.warning("failed to modulate #%s. Got %s and %s with fail.", label["color"], fg.hex, bg.hex)
                    fg = "#" + label["color"]
                    bg = "#000"
                labels.append(f"[{colour_span(label['name'], fg=fg, bg=bg)}]({url})")
            if labels:
                line += " (" + " ".join(labels) + ")"
            lines.append(line)
        if not lines:
            return
        o = "\n".join(lines)
        reply_id = await evt.reply(o, markdown=True, allow_html=True)
        for k in cache_set:
            self.last_sent[k] = now
        await self.client.react(evt.room_id, reply_id, WASTEBASKET)

    @command.passive("MSC(\d{4})", multiple=True, case_insensitive=True)
    async def on_msc_number(self, evt: MessageEvent, matches: list[tuple[str]]):
        if evt.content.relates_to.rel_type == "m.replace" or evt.content.body.startswith("* "):
            return  # don't react to message edits
        now = time.time()
        await self.client.set_fully_read_marker(evt.room_id, evt.event_id, evt.event_id)
        lines = []
        cache_set = set()
        async with self.getter_lock:
            to_get = set()
            for match_set in matches:
                m = list(match_set)
                full = m.pop(0)
                n = int(m.pop())
                if n in to_get:
                    continue
                to_get.add(n)
                cache_key = f"{evt.room_id};MSC;{n}"
                last_sent = self.last_sent.get(cache_key, 0)
                if now - last_sent < 60:
                    self.log.info("Ignoring request for MSC%d as it was sent %.1fs ago", n, now - last_sent)
                    await self.client.react(evt.room_id, evt.event_id, "⏳")
                    continue
                self.log.info("Fetching MSC %d", n)

                info = await self.get_issue(n, "matrix-org/matrix-spec-proposals", base_url="https://api.github.com")
                if isinstance(info, Exception):
                    self.log.error("Error while fetching %s: %s", full, info, exc_info=info)
                    continue
                if info is None:
                    lines.append(f"* `MSC{n:04d}`: not found")
                    continue
                cache_set.add(cache_key)
                title = info.get("title", "(no title)")
                if title.startswith("MSC"):
                    title = title.split(" ", 1)[1]
                line = (
                    "* [MSC{0:04d}]({1[html_url]}) - {2} by [@{1[user][login]}]({1[user][html_url]})".format(
                        n, info, title
                    )
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                labels = []
                for label in info["labels"]:
                    url = (
                        "https://github.com/matrix-org/matrix-spec-proposals/pulls?q=is%3Apr+is%3Aopen+label%3A"
                        + quote(label["name"])
                    )
                    fg, bg, ok = modulate_colour(
                        "#" + label["color"], "#212830", level=AccessibilityLevel.AAA, mode=ModulationMode.FOREGROUND
                    )
                    if ok:
                        self.log.debug("modulated #%s to %s on %s with success.", label["color"], fg.hex, bg.hex)
                        fg = fg.hex
                        bg = bg.hex
                    else:
                        self.log.warning(
                            "failed to modulate #%s. Got %s and %s with fail.", label["color"], fg.hex, bg.hex
                        )
                        fg = "#" + label["color"]
                        bg = "#000"
                    labels.append(f"[{colour_span(label['name'], fg=fg, bg=bg)}]({url})")
                if labels:
                    line += " (" + " ".join(labels) + ")"
                lines.append(line)

        if not lines:
            return
        o = "\n".join(lines)
        reply_id = await evt.reply(o, markdown=True, allow_html=True)
        for k in cache_set:
            self.last_sent[k] = now
        await self.client.react(evt.room_id, reply_id, WASTEBASKET)

    @command.new("resolve")
    @command.argument("server_name", required=True)
    async def resolve_server(self, evt: MessageEvent, server_name: str) -> None:
        """Performs server-to-server and client-to-server resolution for a Matrix server.

        Usage: !resolve <server>

        Example: !resolve matrix.org
        """
        if AsyncServerResolver is None:
            await evt.reply("This command is not currently available.")
            return

        await self.client.set_typing(evt.room_id, 60_000)
        output = []
        start = time.perf_counter()
        try:
            self.log.info("Resolving server %s", server_name)
            result = await self.server_resolver.resolve(server_name)
            result_str = "\n* Host: `{0.host_header}`\n* TLS name: `{0.sni}`\n* Resolution step: {1}\n".format(
                result,
                S2S_STEPS.get(result._step, "unrecognised step") + f" ({result._step})",
            )
            self.log.debug("Resolved %s to %r", server_name, result)
            e2 = time.perf_counter() - start
        except Exception as e:
            self.log.error("Error while resolving server %s: %s", server_name, e, exc_info=e)
            e2 = time.perf_counter() - start
            output.append(f"{CROSS} Failed to resolve server-to-server after {e2:.2f}s: `{e}`")
        else:
            try:
                ver = list(await self.server_resolver.get_server_version(result))
                ver[0] = ver[0] or "Unknown"
                ver[1] = ver[1] or "Unknown"
                result_str += f"* Advertised version: `{ver[0]}/{ver[1]}`\n"
            except Exception as e:
                self.log.error("Error while fetching server version for %s: %s", server_name, e, exc_info=e)
                output.append(
                    f"{WARNING_SIGN} Resolved server-to-server after {e2:.2f}s: {result_str}\nBut could not"
                    f" fetch server version: `{e}`"
                )
            else:
                try:
                    keys = await self.server_resolver.get_server_keys(result)
                    result_str += f"* Signing keys: {', '.join(keys.verify_keys.keys())}\n"
                except Exception as e:
                    self.log.error("Error while fetching server keys for %s: %s", server_name, e, exc_info=e)
                    output.append(
                        f"{WARNING_SIGN} Resolved server-to-server after {e2:.2f}s: {result_str}\n"
                        f"But could not fetch server signing keys: `{e}`"
                    )
                else:
                    output.append(f"{CHECKMARK} Resolved server-to-server after {e2:.2f}s: {result_str}")

        start = time.perf_counter()
        try:
            self.log.info("Resolving client %s", server_name)
            result = await self.client_resolver.resolve(server_name, extra_validation=False)
            e2 = time.perf_counter() - start
            self.log.debug("Resolved %s to %r, fetching versions", server_name, result)
        except Exception as e:
            self.log.error("Error while resolving client %s: %s", server_name, e, exc_info=e)
            e2 = time.perf_counter() - start
            output.append(f"{CROSS} Failed to resolve client-to-server after {e2:.2f}s: `{e}`")
        else:
            try:
                ver = await self.client_resolver.get_client_versions(result)
            except Exception as e:
                self.log.error("Error while fetching client versions for %s: %s", server_name, e, exc_info=e)
                output.append(
                    f"{WARNING_SIGN} Resolved client-to-server after {e2:.2f}s: {result}, but could not"
                    f" fetch client versions: `{e}`"
                )
            else:
                output += [
                    f"{CHECKMARK} Resolved client-to-server after {e2:.2f}s: {result}"
                    f" (versions: {', '.join(ver.versions)})"
                ]

        await evt.reply("\n\n".join(output), markdown=True, allow_html=False)
        await self.client.set_typing(evt.room_id, 0)

    @command.new("version")
    @command.argument("server_name", required=True)
    async def resolve_version(self, evt: MessageEvent, server_name: str) -> None:
        """Fetches the advertised version of a Matrix server.

        Usage: !version <server>

        Example: !version matrix.org
        """
        if AsyncServerResolver is None:
            await evt.reply("This command is not currently available.")
            return

        await self.client.set_typing(evt.room_id, 60_000)
        try:
            destination = await self.server_resolver.resolve(server_name)
            await self.client.set_typing(evt.room_id, 0)
        except Exception as e:
            await self.client.set_typing(evt.room_id, 0)
            self.log.error("Error while resolving server %s: %s", server_name, e, exc_info=e)
            await evt.reply(f"{CROSS} Failed to resolve server: `{e}`")
            return

        # get the advertised federation version
        fed = self.server_resolver.create_request(destination, "GET", "/_matrix/federation/v1/version")
        try:
            resp = await self.server_resolver.client.send(fed)
            data = resp.json()
            if not isinstance(data, dict):
                self.log.warning("Unexpected response type for version from %s: %r", server_name, data)
                await evt.reply(
                    f"{WARNING_SIGN} Resolved server to {destination}, but got malformed version response: `{data}`"
                )
                return

            version = data.get("server", {})
            if not isinstance(version, dict):
                self.log.warning("Unexpected 'server' field type for version from %s: %r", server_name, version)
                await evt.reply(
                    f"{WARNING_SIGN} Resolved server to {destination}, but got malformed version data: `{data}`"
                )
                return
            name = version.get("name", "Unknown")
            ver = version.get("version", "Unknown")
            msg = f"\N{WHITE HEAVY CHECK MARK} Reported version: `{name}/{ver}`"
            if name == "Unknown" or ver == "Unknown":
                msg += f" (raw response: `{json.dumps(data)}`)"
            await evt.reply(msg)
            return
        except Exception as e:
            self.log.error("Error while resolving server %s: %s", server_name, e, exc_info=e)
            await evt.reply(
                f"{WARNING_SIGN} Resolved server to {destination}, but failed to fetch federation version: `{e}`"
            )
