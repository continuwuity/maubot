import asyncio
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
    cs: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.config.load_and_update()
        self.cs = aiohttp.ClientSession(
            base_url=self.forge
        )

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

    async def get_issue(self, n: int, repo: str = "continuwuation/continuwuity") -> dict | None | Exception:
        try:
            async with self.http.get(self.base_url + "/repos/%s/issues/%d" % (repo, n)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return None
            return e

    async def get_pull_request(self, n: int, repo: str = "continuwuation/continuwuity") -> dict | None | Exception:
        """
        Get a pull request by number.

        :param n: The pull request number
        :param repo: The repository to pull from. Defaults to "continuwuation/continuwuity"
        :return: The data, None if not found
        """
        try:
            async with self.http.get(self.base_url + "/repos/%s/pulls/%d" % (repo, n)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return None
            return e

    @command.passive(r"([a-zA-Z]+/)?([a-zA-Z]+)?[#!](\d+)", multiple=True)
    async def on_issue_number(self, evt: MessageEvent, matches: list[tuple[str]]):
        await self.client.set_typing(evt.room_id, 30_000)
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
                "* [#{0[number]} ({0[state]}): {0[title]}]({0[html_url]}) by [{0[user][username]}]({0[user][html_url]})".format(
                    result
                )
            )
        if not lines:
            return
        o = "\n".join(lines)
        await evt.reply(o, markdown=True, allow_html=False)
