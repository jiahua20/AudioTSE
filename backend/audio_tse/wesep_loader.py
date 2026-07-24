from __future__ import annotations

"""Runtime shim that lets ``import wesep`` / ``load_model_local`` work against a
pristine, unmodified pip install - no edits to anything under site-packages.

Why this exists
---------------
``install-wesep-tse.ps1`` installs wesep from GitHub and wespeaker with
``--no-deps``. Two things in those packages break ``import wesep`` for the
BSRNN+ECAPA TSE path, even though the TSE model itself needs none of them:

  1. The wesep wheel omits the ``wesep.utils`` package, yet
     ``wesep.cli.extractor`` imports ``load_pretrained_model`` and ``set_seed``
     from it at module load time.
  2. Several optional submodules (the Speaker CLI, s3prl/whisper/w2vbert
     frontends, convtasnet/dpccn/tfgridnet backends, ...) pull heavy deps
     (kaldiio, transformers, whisper, ...) that ``--no-deps`` never installed.

An earlier approach rewrote files inside site-packages to fix this. This module
achieves the same result purely in-process, *before* ``import wesep`` runs, so
no installed source file is ever touched:

  * The vendored ``wesep.utils`` (shipped next to this file under ``_wesep_utils``)
    is registered into ``sys.modules`` as a real package with a real ``__path__``.
  * The optional submodules are pre-seeded as inert stubs, so the bare
    ``import X`` lines in the installed packages resolve to a stub instead of
    executing the broken module body.

Call ``ensure_wesep_runtime()`` once, before importing wesep. Idempotent.
"""

import importlib.util
import sys
import types
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent / "_wesep_utils"

# Submodules that genuinely fail to import under ``--no-deps`` (probed against
# a pristine wesep+wespeaker install), and whose symbols the BSRNN+ECAPA TSE
# path never uses. Pre-seeded as stubs so the bare ``import X`` /
# ``from X import Y`` lines in the installed packages resolve to a stub instead
# of executing the broken module body.
#
# Rule: only stub modules that actually fail. Stubbing a *clean* nested leaf
# (e.g. wesep.modules.metric_gan.discriminator) while its parent package is not
# yet imported corrupts the parent import with
# "cannot import name '<leaf>' from '<parent>'". The other wesep/wespeaker
# backends (convtasnet, dpccn, tfgridnet, bsrnn_multi_optim, metric_gan's
# discriminator, whisper_PMFA, w2vbert_adapter_mfa) import cleanly on a
# --no-deps install and are intentionally left alone.
# Maps dotted module name -> attributes to expose on the stub.
_OPTIONAL_STUBS: dict[str, dict[str, object]] = {
    # wespeaker: Speaker CLI pulls umap/kaldiio/diar deps.
    "wespeaker.cli.speaker": {"load_model": None, "load_model_pt": None},
    # wespeaker: optional frontends (s3prl / whisper / w2vbert).
    "wespeaker.frontend.s3prl": {"S3prlFrontend": None},
    "wespeaker.frontend.whisper_encoder": {"whisper_encoder": None},
    "wespeaker.frontend.w2vbert": {"W2VBertFrontend": None},
    # wesep: alt backend that needs wesep.utils.funcs (also omitted by wheel).
    # Only bsrnn is used by this model, so the stub is never routed to.
    "wesep.models.bsrnn_feats": {},
}


def _seed_stub(dotted: str, attrs: dict[str, object]) -> None:
    """Register an inert module under ``dotted`` unless one is already loaded."""
    if sys.modules.get(dotted) is not None:
        return
    mod = types.ModuleType(dotted)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[dotted] = mod


def _install_vendored_utils() -> None:
    """Expose the shipped ``_wesep_utils`` as the ``wesep.utils`` package.

    A regular package (``wesep`` has an ``__init__.py``) cannot be extended on
    another ``sys.path`` entry, so we cannot shadow ``wesep.utils`` via
    PYTHONPATH. Instead we register the vendored files directly under their
    expected dotted names with a real ``__path__``, which lets Python resolve
    ``from wesep.utils.checkpoint import load_pretrained_model`` to our copies.
    """
    if sys.modules.get("wesep.utils.utils") is not None:
        return  # already installed - idempotent

    pkg = types.ModuleType("wesep.utils")
    pkg.__path__ = [str(_VENDOR)]  # marks it as a package
    pkg.__package__ = "wesep.utils"
    sys.modules["wesep.utils"] = pkg

    # schedulers/utils first: checkpoint.py does
    # ``from wesep.utils.schedulers import BaseClass`` at import time.
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
    """Make ``import wesep`` succeed against a pristine install. Idempotent."""
    _install_vendored_utils()
    for dotted, attrs in _OPTIONAL_STUBS.items():
        _seed_stub(dotted, attrs)
