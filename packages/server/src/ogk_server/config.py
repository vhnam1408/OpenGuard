from dataclasses import dataclass, field
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str = field(repr=False)
    policy_secret: str = field(repr=False)
    issuer: str = "ogk-server"
    audience: str = "ogk-api"
    refresh_days: int = 30

    def __post_init__(self) -> None:
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("OGK_DATABASE_URL must be a SQLite file URL")
        if self.database_url.endswith(":memory:"):
            raise ValueError("Use a file database so WAL and multiple connections work")
        if min(len(self.jwt_secret.encode()), len(self.policy_secret.encode())) < 32:
            raise ValueError("JWT and policy secrets must each contain at least 32 bytes")
        if self.jwt_secret == self.policy_secret:
            raise ValueError("Use independent JWT and policy secrets")
        if not 1 <= self.refresh_days <= 90:
            raise ValueError("OGK_REFRESH_DAYS must be between 1 and 90")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["OGK_DATABASE_URL"],
            jwt_secret=os.environ["OGK_JWT_SECRET"],
            policy_secret=os.environ["OGK_POLICY_SECRET"],
            refresh_days=int(os.environ.get("OGK_REFRESH_DAYS", "30")),
        )
