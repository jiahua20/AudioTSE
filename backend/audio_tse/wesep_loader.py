from __future__ import annotations

"""运行时垫片（shim），让 ``import wesep`` / ``load_model_local`` 能在一个干净、
未经修改的 pip 安装环境下工作——无需改动 site-packages 下的任何文件。

为什么需要它
------------
``install-wesep-tse.ps1`` 从 GitHub 安装 wesep，并以 ``--no-deps`` 安装 wespeaker。
这两个包里有两处问题会导致 BSRNN+ECAPA TSE 路径无法 ``import wesep``，
尽管 TSE 模型本身根本用不到它们：

  1. wesep 的 wheel 包遗漏了 ``wesep.utils`` 包，但 ``wesep.cli.extractor``
     在模块加载时就会从中导入 ``load_pretrained_model`` 和 ``set_seed``。
  2. 若干可选子模块（Speaker CLI、s3prl/whisper/w2vbert 前端、
     convtasnet/dpccn/tfgridnet 后端等）会拉入重量级依赖
     （kaldiio、transformers、whisper 等），而这些依赖 ``--no-deps`` 根本没装。

早先的做法是直接改写 site-packages 内的文件来修复。本模块则完全在进程内、
在 ``import wesep`` 执行 *之前* 达成相同效果，因此任何已安装的源文件都不会被触碰：

  * 内嵌的 ``wesep.utils``（随本文件一同发布，位于 ``_wesep_utils`` 下）会被
    作为一个真正的包、带有真正的 ``__path__`` 注册进 ``sys.modules``。
  * 可选子模块会被预先注入为惰性 stub（桩模块），这样已安装包里那些裸写的
    ``import X`` 行就会解析到桩上，而不是去执行那段有问题的模块体。

在导入 wesep 之前调用一次 ``ensure_wesep_runtime()`` 即可。幂等。
"""

import importlib.util
import sys
import types
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent / "_wesep_utils"

# 在 ``--no-deps`` 安装下确实无法导入（已在一个干净的 wesep+wespeaker 安装中
# 验证过）、且其符号从不被 BSRNN+ECAPA TSE 路径使用的子模块。预先注入为桩模块，
# 使得已安装包里裸写的 ``import X`` / ``from X import Y`` 行解析到桩上，
# 而不是去执行那段有问题的模块体。
#
# 规则：只对真正会导入失败的模块打桩。如果给一个 *正常的* 嵌套叶子模块
# （例如 wesep.modules.metric_gan.discriminator）打桩，而它的父包尚未导入，
# 就会污染父包的导入，报 "cannot import name '<leaf>' from '<parent>'"。
# 其余 wesep/wespeaker 后端（convtasnet、dpccn、tfgridnet、bsrnn_multi_optim、
# metric_gan 的 discriminator、whisper_PMFA、w2vbert_adapter_mfa）在
# --no-deps 安装下可正常导入，故有意保持不动。
# 映射：点分模块名 -> 在桩上暴露的属性。
_OPTIONAL_STUBS: dict[str, dict[str, object]] = {
    # wespeaker：Speaker CLI 会拉入 umap/kaldiio/diar 相关依赖。
    "wespeaker.cli.speaker": {"load_model": None, "load_model_pt": None},
    # wespeaker：可选前端（s3prl / whisper / w2vbert）。
    "wespeaker.frontend.s3prl": {"S3prlFrontend": None},
    "wespeaker.frontend.whisper_encoder": {"whisper_encoder": None},
    "wespeaker.frontend.w2vbert": {"W2VBertFrontend": None},
    # wesep：备选后端，依赖 wesep.utils.funcs（同样被 wheel 遗漏）。
    # 本模型只用 bsrnn，所以这个桩永远不会被路由到。
    "wesep.models.bsrnn_feats": {},
}


def _seed_stub(dotted: str, attrs: dict[str, object]) -> None:
    """在 ``dotted`` 名下注册一个惰性模块；若该模块已加载则跳过。"""
    if sys.modules.get(dotted) is not None:
        return
    mod = types.ModuleType(dotted)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[dotted] = mod


def _install_vendored_utils() -> None:
    """将随包发布的 ``_wesep_utils`` 暴露为 ``wesep.utils`` 包。

    常规包（``wesep`` 自带 ``__init__.py``）无法在另一个 ``sys.path`` 条目上
    被扩展，因此无法通过 PYTHONPATH 来覆盖 ``wesep.utils``。作为替代，我们
    直接以内嵌文件预期的点分名字注册它们，并赋予真实的 ``__path__``，
    使 Python 能把 ``from wesep.utils.checkpoint import load_pretrained_model``
    解析到我们的副本上。
    """
    if sys.modules.get("wesep.utils.utils") is not None:
        return  # 已安装——幂等

    pkg = types.ModuleType("wesep.utils")
    pkg.__path__ = [str(_VENDOR)]  # marks it as a package
    pkg.__package__ = "wesep.utils"
    sys.modules["wesep.utils"] = pkg

    # 先装 schedulers/utils：checkpoint.py 在导入时会执行
    # ``from wesep.utils.schedulers import BaseClass``。
    for name in ("schedulers", "utils", "checkpoint"):
        dotted = f"wesep.utils.{name}"
        spec = importlib.util.spec_from_file_location(dotted, _VENDOR / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load vendored module {dotted}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[dotted] = mod
        setattr(pkg, name, mod)
        spec.loader.exec_module(mod)


def ensure_wesep_runtime() -> None:
    """让 ``import wesep`` 在干净的安装下成功执行。幂等。"""
    _install_vendored_utils()
    for dotted, attrs in _OPTIONAL_STUBS.items():
        _seed_stub(dotted, attrs)
