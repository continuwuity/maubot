import asyncio
from typing import Type
from urllib.parse import quote

import aiohttp
from maubot import MessageEvent, Plugin
from maubot.handlers import command, event
from mautrix.types import EventType, ReactionEvent
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

from .vendor.color_contrast import AccessibilityLevel, ModulationMode
from .vendor.color_contrast import modulate as modulate_colour


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
    cs: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.config.load_and_update()
        self.cs = aiohttp.ClientSession(base_url=self.forge)

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
        return self.config.get("forge", "https://forgejo.ellis.link")

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
        if evt.content.relates_to.key.strip() != "🗑️":
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
        await self.client.set_fully_read_marker(evt.room_id, evt.event_id, evt.event_id)
        t: list[asyncio.Task] = []
        async with asyncio.TaskGroup() as tg:
            for match_set in matches:
                m = list(match_set)
                full = m.pop(0)
                org = m.pop(0) or self.main_org
                # Trim trailing slash if present
                if org.endswith("/"):
                    org = org[:-1]
                repo = m.pop(0) or self.main_repo
                n = int(m.pop(0))
                self.log.info("Fetching issue %d from %s/%s", n, org, repo)
                t.append(tg.create_task(self.get_issue(n, f"{org}/{repo}"), name=full))

        lines = []
        for task in t:
            result = task.result()
            if result is None:
                continue
            elif isinstance(result, Exception):
                self.log.error("Error while fetching %s: %s", task.get_name(), result, exc_info=result)
                continue
            line = (
                "* [#{0[number]} ({0[state]}): {0[title]}]({0[html_url]}) by "
                "[{0[user][username]}]({0[user][html_url]})".format(result).replace("<", "&lt;").replace(">", "&gt;")
            )
            labels = []
            for label in result["labels"]:
                url = "https://forgejo.ellis.link/continuwuation/continuwuity/{}s?q=&labels={!s}".format(
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
        await evt.reply(o, markdown=True, allow_html=True)

    @command.passive("MSC(\d{4})", multiple=True, case_insensitive=True)
    async def on_msc_number(self, evt: MessageEvent, matches: list[tuple[str]]):
        await self.client.set_fully_read_marker(evt.room_id, evt.event_id, evt.event_id)
        lines = []
        for match_set in matches:
            m = list(match_set)
            full = m.pop(0)
            n = int(m.pop())

            info = await self.get_issue(n, "matrix-org/matrix-spec-proposals", base_url="https://api.github.com")
            if isinstance(info, Exception):
                self.log.error("Error while fetching %s: %s", full, info, exc_info=info)
                continue
            if info is None:
                lines.append(f"* `MSC{n:04d}`: not found")
                continue
            title = info.get("title", "(no title)")
            if title.startswith("MSC"):
                title = title.split(" ", 1)[1]
            line = (
                "* [MSC{0:04d}]({1[html_url]}) - {2} by [@{1[user][login]}]({1[user][html_url]})".format(n, info, title)
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            labels = []
            for label in info["labels"]:
                url = "https://github.com/matrix-org/matrix-spec-proposals/pulls?q=is%3Apr+is%3Aopen+label%3A" + quote(
                    label["name"]
                )
                fg, bg, ok = modulate_colour(
                    "#" + label["color"], "#212830", level=AccessibilityLevel.AAA, mode=ModulationMode.FOREGROUND
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
        await evt.reply(o, markdown=True, allow_html=True)
