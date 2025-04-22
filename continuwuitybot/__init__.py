import asyncio
import re
from typing import Type

import aiohttp
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

from maubot import Plugin, MessageEvent
from maubot.handlers import command


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("repo")
        helper.copy("repo.owner")
        helper.copy("repo.name")
        helper.copy("forge")

class ContinuwuityHelper(Plugin):
    RE_ISSUE_N = re.compile(r"((\w+)?/?(\w+))?#(\d+)")
    cs: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.config.load_and_update()
        self.cs = aiohttp.ClientSession(base_url=self.forge + "/api/v1")

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

    async def get_issue(self, n: int, repo: str = "continuwuation/continuwuity") -> dict | None | Exception:
        try:
            async with self.cs.get("/repos/%s/issues/%d" % (repo, n)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return None
            return e

    @command.passive(RE_ISSUE_N, multiple=True)
    async def on_issue_number(self, evt: MessageEvent, matches: list[tuple[str]]):
        t: list[asyncio.Task] = []
        async with asyncio.TaskGroup() as tg:
            for match_set in matches:
                m = list(match_set)
                full = m.pop(0)
                org = self.main_org
                # don't actually use any of the regex groups for groups. regex sucks.
                p, n = full.rsplit("#", 1)
                try:
                    n = int(n)
                except ValueError:
                    # invalid
                    continue
                if "/" in p:
                    org, repo = p.split("/", 1)
                else:
                    repo = p
                t.append(
                    tg.create_task(self.get_issue(n, f"{org}/{repo}"), name=full)
                )

        lines = []
        for task in t:
            result = task.result()
            if result is None:
                continue
            elif isinstance(result, Exception):
                self.log.error("Error while fetching %s: %s", task.get_name(), result, exc_info=result)
                continue
            lines.append(
                "* [#{0.number} ({0.state}): {0.title}]({0.html_url}) by [{0.user.username}]({0.user.html_url})".format(
                    result
                )
            )
        if not lines:
            return
        o = "\n".join(lines)
        await evt.reply(o, markdown=True, allow_html=False)
