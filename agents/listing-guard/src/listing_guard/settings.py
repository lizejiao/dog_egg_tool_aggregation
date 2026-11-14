"""全局配置：环境变量（.env）+ config/*.yaml 合并加载。

策略版本号 = factors.yaml + thresholds.yaml 内容哈希前 8 位，
随 state 落检查点，任何一次判定都能回溯当时用的策略。
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"


class EnvSettings(BaseSettings):
    """环境变量层（.env）。"""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    audit_mode: str = "mock"  # mock | live
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    vision_model: str = "gpt-4o-mini"
    text_model: str = "gpt-4o-mini"
    # LangSmith 追踪（env 桌 gating，未配 key 时零开销跳过）
    langsmith_api_key: str = ""
    langsmith_project: str = "listing-guard"
    langsmith_endpoint: str = "https://api.smith.langchain.com"


class FactorConf(BaseModel):
    id: str
    name: str
    kind: str  # deterministic | probabilistic
    weight: float
    veto: bool = False
    veto_style: str = "reject"  # veto 命中后的去向：reject | human


class Thresholds(BaseModel):
    pass_: float = Field(alias="pass")
    reject: float


class ComplianceConf(BaseModel):
    injection_patterns: list[str] = []
    banned_words: list[str] = []
    contact_patterns: list[str] = []


class AgentConf(BaseModel):
    checkpoint_db: str = "data/checkpoints.sqlite"
    gov_pending_file: str = "data/gov_pending.json"
    feedback_dir: str = "data/feedback"
    tool_timeout_ms: int = 5000


class GrayRelease(BaseModel):
    cities: list[str] = []
    ratio: float = 1.0


class Settings:
    """全量配置（yaml + env 合并）。"""

    def __init__(self) -> None:
        self.env = EnvSettings()
        agent_doc = _load_yaml(CONFIG_DIR / "agent.yaml")
        self.agent = AgentConf(**agent_doc["agent"])
        self.gray = GrayRelease(**agent_doc.get("gray_release", {}))

        factors_doc = _load_yaml(CONFIG_DIR / "factors.yaml")
        self.factors: dict[str, FactorConf] = {f.id: f for f in (FactorConf(**x) for x in factors_doc["factors"])}

        th_doc = _load_yaml(CONFIG_DIR / "thresholds.yaml")
        self.thresholds = Thresholds(**th_doc["default"])
        self.city_thresholds: dict[str, dict] = th_doc.get("cities", {}) or {}

        comp_doc = _load_yaml(CONFIG_DIR / "compliance.yaml")
        self.compliance = ComplianceConf(**comp_doc)

        # 策略版本：因子表 + 阈值内容哈希，落检查点可回溯
        raw = (CONFIG_DIR / "factors.yaml").read_bytes() + (CONFIG_DIR / "thresholds.yaml").read_bytes()
        self.policy_version = hashlib.sha1(raw).hexdigest()[:8]

        # 种子数据
        seed_comm = _load_yaml(DATA_DIR / "seed" / "communities.yaml")
        self.communities: dict[str, dict] = {c["name"]: c for c in seed_comm["communities"]}
        seed_agents = _load_yaml(DATA_DIR / "seed" / "agents.yaml")
        self.publisher_agents: dict[str, dict] = {a["id"]: a for a in seed_agents["agents"]}

    def thresholds_for(self, city: str) -> Thresholds:
        override = self.city_thresholds.get(city)
        if override:
            return Thresholds(**override)
        return self.thresholds

    @property
    def checkpoint_path(self) -> Path:
        return PROJECT_ROOT / self.agent.checkpoint_db

    @property
    def gov_pending_path(self) -> Path:
        return PROJECT_ROOT / self.agent.gov_pending_file

    @property
    def feedback_dir(self) -> Path:
        return PROJECT_ROOT / self.agent.feedback_dir


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
