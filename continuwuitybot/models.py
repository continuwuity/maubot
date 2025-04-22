import dataclasses


@dataclasses.dataclass(frozen=True)
class User:
    active: bool
    avatar_url: str
    created: str
    description: str
    email: str
    followers_count: int
    following_count: int
    full_name: str
    html_url: str
    id: int
    is_admin: bool
    language: str
    last_login: str
    prohibit_login: bool
    pronouns: str
    restricted: bool
    source_id: int
    visibility: str
    website: str


@dataclasses.dataclass(frozen=True)
class Issue:
    assets: list
    assignee: User
    assignees: list[User]
    body: str
