"""Provider 抽象层：所有语音引擎的统一契约。

这是本框架的核心（docs/ARCHITECTURE.md §4.2）。新增一个引擎 = 新增一个
TTSProvider 子类，业务代码（任务引擎、API、WebUI）零改动。

契约要点：
- voice_key：引擎内全局唯一。约定格式：
    clone:<本地uuid>            —— 上传克隆音色（本地 DB 持有）
    preset:<model_id>           —— 引擎自带/云端音色（引擎侧持有）
  Provider 收到 voice_key 后自行解析；克隆音色经 register 后引擎侧持有引用。
- synthesize()：输入单段文本，输出音频文件路径。失败抛 TTSProviderError，
  由调用方根据 retryable 决定重试策略。
- 一切日志输出必须脱敏：provider 实现里用自己的 api_key 调 config.sanitize()。
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vss.providers")


# ---------------------------------------------------------------- 数据类型

@dataclass
class Caps:
    """引擎能力声明，前端据此渲染 UI（如情绪标签开关显隐）。"""
    kind: str                      # "cloud" | "local" | "mock"
    display_name: str               # WebUI 显示名
    max_concurrency: int = 1       # 段级并发上限
    supports_emotion_tags: bool = False   # 是否支持 [pause] 等内联标签
    sample_rate_hz: int = 24000    # 引擎输出采样率（拼接层据此归一）
    has_preset_voices: bool = False       # 是否有自带音色
    needs_registration: bool = True       # 克隆音色是否需先注册（本地克隆型=是，mock=否）
    # 支持的合成参数（2026-11-15 新增，P2-0）：参数名 → 是否支持。
    # 前端按此**动态渲染参数面板，不支持的不显示**（不置灰）——
    # 引擎能力变更只改此处一处声明，禁止前端硬编码。
    # 当前键约定：speed（语速倍数）/ volume（音量，Fish 为 dB 偏移）。
    supported_params: Dict[str, bool] = field(
        default_factory=lambda: {"speed": False, "volume": False})
    notes: str = ""


@dataclass
class VoiceMeta:
    """音色元信息（预置与克隆统一形态）。"""
    voice_key: str                  # 约定见模块 docstring
    name: str
    state: str = "trained"          # 云端克隆音色可能有 created/training/trained/failed
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthParams:
    """合成参数。取值只在引擎声明 supported_params 后才会被传入（runner 侧守门）。

    - speed：语速倍数（0.5~2.0），CosyVoice3/Fish 均原生支持
    - volume_db：音量 dB 偏移（-20~20，0=不改）；仅 Fish（prosody.volume 单位是 dB）
    """
    speed: float = 1.0
    volume_db: float = 0.0
    emotion_tags_allowed: bool = True  # 由调用方按 Caps 决定是否传标签文本


class TTSProviderError(Exception):
    """引擎调用失败。retryable=True 的错误（429/5xx/超时/OOM）走重试退避。"""

    def __init__(self, message: str, retryable: bool = False, status: int = 0):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


# ---------------------------------------------------------------- 抽象基类

class TTSProvider(ABC):
    """语音引擎抽象基类。"""

    id: str = "base"                # 全局唯一标识（与 config.providers 键对齐）

    def __init__(self, cfg: Dict[str, Any]):
        """所有 provider 统一以自身配置字典构造（工厂约定）。

        子类按需读取配置项；不需要配置的引擎无需覆写。
        """
        self.cfg = cfg or {}

    # ------------------------------------------------------------ 必须实现

    @abstractmethod
    def capabilities(self) -> Caps:
        """声明引擎能力。"""

    @abstractmethod
    def synthesize(self, text: str, voice_key: str,
                   out_path: str, params: SynthParams) -> Dict[str, Any]:
        """合成单段语音。

        Returns: {"audio_path": str, "duration_ms": int, "sample_rate_hz": int}
        Raises:  TTSProviderError（retryable 标记可否重试）
        """

    # ------------------------------------------------------------ 可选实现

    def test_connection(self) -> Dict[str, Any]:
        """连通性/健康检查。Returns: {"ok": bool, "message": str}"""
        return {"ok": True, "message": "无外部依赖"}

    def warmup(self) -> Dict[str, Any]:
        """预热引擎（本地大模型加载权重、云端建连等）。

        默认实现：无需暖机。慢启动引擎（如 CosyVoice3 本地）覆写此方法。
        """
        return {"ok": True, "message": "该引擎无需暖机"}

    def list_preset_voices(self) -> List[VoiceMeta]:
        """列出引擎自带音色。无预置音色的引擎返回空列表。"""
        return []

    def register_clone_voice(self, ref_path: str, ref_text: str,
                             name: str) -> VoiceMeta:
        """把本地克隆音色注册到引擎（云端建模型 / 本地记录引用）。

        无需注册的引擎（mock）直接回显。
        """
        return VoiceMeta(voice_key=f"clone:{name}", name=name)

    def delete_voice(self, voice_key: str) -> None:
        """删除引擎侧音色（云端模型 / 本地引用）。默认无操作。"""

    def strip_emotion_tags(self, text: str) -> str:
        """剥离 [tag] 形式的情绪标签（引擎不支持时由调用方调用）。"""
        import re
        # 只剥 ASCII 词标签（如 [pause]、[excited tone]），中文方括号内容（如 [停顿]）不误伤
        return re.sub(r"\[[A-Za-z][A-Za-z ,.'-]*\]", "", text)


def strip_or_keep_tags(text: str, caps: Caps, params: SynthParams) -> str:
    """工具函数：按引擎能力与用户选择决定标签文本去留。"""
    if caps.supports_emotion_tags and params.emotion_tags_allowed:
        return text
    # 不支持或用户关闭 → 用基类默认剥离逻辑
    return TTSProvider.strip_emotion_tags(TTSProvider, text)
